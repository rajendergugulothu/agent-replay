"""The FastAPI app: traces, diagnoses, resume requests and results."""

import json
import logging
import shutil
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from agent_replay import __version__
from agent_replay.api.models import (
    RUN_ID_PATTERN,
    CompareOut,
    DiagnosisOut,
    HealthOut,
    LocalizeIn,
    ResetOut,
    ResumeIn,
    RunCost,
    RunOut,
    RunStarted,
    ScenarioOut,
    ScheduleEntry,
    StartRunIn,
    StepOut,
    TokenSplit,
    ToolOut,
)
from agent_replay.catalog import load_catalog
from agent_replay.config import Settings
from agent_replay.experiments.cost import recovery_cost
from agent_replay.injection import Scenario, load_scenario, load_scenarios, scenario_path
from agent_replay.llm import OfflineMiss, make_client
from agent_replay.localizers import diagnose, load_diagnosis, make_localizer
from agent_replay.models import FailureType, StoredRun, StoredStep, ToolCall
from agent_replay.planner.agent import AgentSettings, initial_state, run_agent
from agent_replay.planner.tools import TOOLS, argument_errors, parameters_schema
from agent_replay.recovery import (
    build_repair_hint,
    run_continuation,
    run_full_restart,
    start_continuation,
    start_full_restart,
    start_scenario_run,
)
from agent_replay.tracing import TraceRecorder

logger = logging.getLogger("agent_replay.api")

RunId = Path(pattern=RUN_ID_PATTERN)

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def summarize_step(step: StoredStep) -> str:
    """One plain line per step for the timeline."""
    if step.tool_name is None:
        return "Final answer"
    args = step.tool_args or {}
    out = step.tool_output or {}
    if "error" in out:
        return f"{step.tool_name} returned an error: {out['error']}"
    if step.tool_name == "get_degree_requirements":
        return f"Fetched degree requirements for {args.get('student_id')}"
    if step.tool_name == "check_prerequisite":
        verdict = "met" if out.get("met") else f"not met, missing {', '.join(out.get('missing', []))}"
        return f"Checked prerequisite for {args.get('course_code')}: {verdict}"
    if step.tool_name == "search_sections":
        after = f" from {args['earliest_start']}" if args.get("earliest_start") else ""
        return f"Searched sections of {args.get('course_code')}{after}: {len(out.get('sections', []))} found"
    if step.tool_name == "build_schedule":
        return f"Built a schedule with {len(args.get('section_ids', []))} sections"
    return str(step.tool_name)


class RateLimiter:
    """Sliding one-minute window per client address, for the endpoints that cost money."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return False
            hits.append(now)
            return True


def create_app(settings: Settings, client: Any | None = None) -> FastAPI:
    """Build the app. Tests pass a fake client. In production the OpenAI
    client is created on first use, so the app still starts without a key."""
    if settings.seed_database_path.is_file() and not settings.database_path.exists():
        # The image ships a read-only database with the frozen experiment. Live
        # visitor runs go into a writable copy that is fine to lose on restart.
        settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(settings.seed_database_path, settings.database_path)
    recorder = TraceRecorder(settings.database_path)
    limiter = RateLimiter(settings.rate_limit_per_minute)
    active = {"count": 0}
    active_lock = threading.Lock()
    client_box: dict[str, Any] = {"client": client}
    agent_settings = AgentSettings(
        model=settings.openai_model or "unset",
        temperature=settings.agent_temperature,
        max_steps=settings.max_steps_per_run,
        max_tokens=settings.max_tokens_per_run,
    )

    app = FastAPI(
        title="Agent Replay",
        version=__version__,
        docs_url=None if settings.production else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.production else "/api/openapi.json",
    )
    if settings.allowed_origin:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[settings.allowed_origin],
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[..., Any]) -> Response:
        request_id = uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response: Response = await call_next(request)
        except Exception:
            # Full detail goes to the log under the request id. The caller
            # gets a plain message and never a stack trace.
            logger.exception(json.dumps({"event": "unhandled_error", "request_id": request_id}))
            response = _error(500, "internal_error", "Something went wrong on the server.")
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = CSP
        if settings.production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        logger.info(
            json.dumps(
                {
                    "event": "request",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "ms": round((time.perf_counter() - started) * 1000),
                }
            )
        )
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'][1:]) or 'request'}: {err['msg']}" for err in exc.errors()
        )
        return _error(422, "invalid_request", problems)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(exc.status_code, "http_error", str(exc.detail))

    def get_client() -> Any:
        if client_box["client"] is None:
            try:
                client_box["client"] = make_client(settings)
            except RuntimeError as exc:
                raise ApiError(503, "not_configured", "The server has no model access configured.") from exc
        return client_box["client"]

    def load(run_id: str) -> StoredRun:
        try:
            return recorder.load_run(run_id)
        except KeyError:
            raise ApiError(404, "run_not_found", "No run with that id.") from None

    def tokens_today() -> int:
        today = datetime.now(UTC).date().isoformat()
        runs = recorder.query(
            "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS n FROM runs WHERE created_at >= ?",
            (today,),
        )[0]["n"]
        judge = recorder.query(
            "SELECT COALESCE(SUM(tokens), 0) AS n FROM diagnoses WHERE created_at >= ?", (today,)
        )[0]["n"]
        return int(runs + judge)

    def rate_limit(request: Request) -> None:
        forwarded = request.headers.get("x-forwarded-for", "") if settings.production else ""
        address = forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")
        if not limiter.allow(address):
            raise ApiError(429, "rate_limited", "Too many requests. Wait a minute and try again.")

    def guard_spend(request: Request) -> None:
        """Every check that protects the API bill runs here, on the server."""
        rate_limit(request)
        if tokens_today() >= settings.daily_token_limit:
            raise ApiError(403, "daily_limit", "Today's token budget for live runs is used up.")

    def launch(run_id: str, work: Callable[[], None]) -> None:
        with active_lock:
            if active["count"] >= settings.max_concurrent_runs:
                # The run row was created before the slot check, so close it.
                recorder.end_run(run_id, status="error", stop_reason="server_busy")
                raise ApiError(429, "busy", "Two runs are already in progress. Try again in a moment.")
            active["count"] += 1

        def target() -> None:
            try:
                work()
            except OfflineMiss:
                _close(run_id, "offline_unavailable")
            except Exception:
                logger.exception(json.dumps({"event": "run_failed", "run_id": run_id}))
                _close(run_id, "api_error")
            finally:
                with active_lock:
                    active["count"] -= 1

        threading.Thread(target=target, daemon=True).start()

    def _close(run_id: str, stop_reason: str) -> None:
        if recorder.load_run(run_id).status == "running":
            recorder.end_run(run_id, status="error", stop_reason=stop_reason)

    def scenario_or_404(scenario_id: str) -> Scenario:
        try:
            scenario = load_scenario(scenario_path(settings.scenarios_dir, scenario_id))
        except KeyError:
            raise ApiError(404, "scenario_not_found", "No scenario with that id.") from None
        if settings.demo_mode and settings.demo_scenarios and scenario_id not in settings.demo_scenarios:
            raise ApiError(403, "not_in_demo", "This scenario is not part of the demo set.")
        return scenario

    def run_out(run: StoredRun) -> RunOut:
        diagnosed = bool(recorder.query("SELECT 1 FROM diagnoses WHERE run_id = ? LIMIT 1", (run.id,)))
        reveal = diagnosed or run.parent_run_id is not None
        catalog = load_catalog()
        schedule = None
        if run.final_schedule is not None:
            schedule = [
                ScheduleEntry(
                    section_id=s.id,
                    course_code=s.course_code,
                    title=catalog.courses[s.course_code].title,
                    days=s.days,
                    start=s.start,
                    end=s.end,
                    credits=catalog.courses[s.course_code].credits,
                )
                for s in (catalog.sections[sid] for sid in run.final_schedule if sid in catalog.sections)
            ]
        return RunOut(
            id=run.id,
            scenario_id=run.scenario_id,
            kind=run.kind,
            status=run.status,
            stop_reason=run.stop_reason,
            parent_run_id=run.parent_run_id,
            branch_step=run.branch_step,
            continuation_mode=run.continuation_mode,
            student_id=run.student_id,
            user_request=run.user_request,
            model=run.model,
            steps=[
                StepOut(
                    step_number=s.step_number,
                    tool_name=s.tool_name,
                    tool_args=s.tool_args,
                    tool_output=s.tool_output,
                    text=s.model_output.get("content"),
                    summary=summarize_step(s),
                    tokens=s.tokens,
                    seconds=s.seconds,
                    depends_on=s.depends_on,
                    origin=s.origin,
                    injection=s.injection if reveal else None,
                )
                for s in run.steps
            ],
            validation=run.validation.model_dump() if run.validation else None,  # type: ignore[arg-type]
            final_schedule=schedule,
            injected_step=run.injected_step if reveal else None,
            observed_failure_step=run.observed_failure_step,
            tokens=run.total_tokens,
            seconds=run.total_seconds,
        )

    def run_cost(run: StoredRun, localization_tokens: int = 0) -> RunCost:
        if run.kind == "original":
            split = TokenSplit(localization=0, repair=0, replay=0, total=run.total_tokens)
        else:
            cost = recovery_cost(run, localization_tokens)
            split = TokenSplit(
                localization=cost.localization_tokens,
                repair=cost.repair_tokens,
                replay=cost.replay_tokens,
                total=cost.total_tokens,
            )
        return RunCost(
            run_id=run.id,
            kind=run.kind,
            status=run.status,
            passed=run.status == "passed",
            tokens=split,
            seconds=run.total_seconds,
            steps_reused=sum(s.origin == "reused" for s in run.steps),
            steps_new=sum(s.origin != "reused" for s in run.steps),
        )

    @app.get("/api/health")
    def health() -> HealthOut:
        return HealthOut(
            status="ok",
            mode="offline" if settings.offline else "live",
            demo_mode=settings.demo_mode,
            version=__version__,
            results_available=(settings.results_dir / "frozen" / "results.json").is_file(),
        )

    @app.get("/api/scenarios")
    def scenarios() -> list[ScenarioOut]:
        labels = {
            r["id"]: r["label_status"] for r in recorder.query("SELECT id, label_status FROM scenarios")
        }
        return [
            ScenarioOut(
                id=s.id,
                title=s.title,
                description=s.description.strip(),
                failure_type=s.failure_type.value,
                student_id=s.task.student_id,
                user_request=s.task.user_request,
                label_status=labels.get(s.id, "unlabeled"),
            )
            for s in load_scenarios(settings.scenarios_dir)
            if not (settings.demo_mode and settings.demo_scenarios and s.id not in settings.demo_scenarios)
        ]

    @app.get("/api/tools")
    def tools() -> list[ToolOut]:
        return [
            ToolOut(name=t.name, description=t.description, parameters=parameters_schema(t))
            for t in TOOLS.values()
        ]

    @app.post("/api/runs", status_code=202)
    def start_run(body: StartRunIn, request: Request) -> RunStarted:
        scenario = scenario_or_404(body.scenario_id)
        guard_spend(request)
        llm = get_client()
        run_id, run_settings, injector = start_scenario_run(
            recorder, scenario, agent_settings, purpose="visitor"
        )

        def work() -> None:
            run_agent(
                client=llm,
                recorder=recorder,
                run_id=run_id,
                student_id=scenario.task.student_id,
                constraints=scenario.task.constraints,
                state=initial_state(scenario.task.student_id, scenario.task.user_request),
                settings=run_settings,
                injector=injector,
            )

        launch(run_id, work)
        return RunStarted(run_id=run_id)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str = RunId) -> RunOut:
        return run_out(load(run_id))

    @app.get("/api/runs/{run_id}/stream")
    def stream_run(run_id: str = RunId) -> StreamingResponse:
        load(run_id)

        def events() -> Iterator[str]:
            sent = 0
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                run = run_out(recorder.load_run(run_id))
                for step in run.steps[sent:]:
                    yield f"event: step\ndata: {step.model_dump_json()}\n\n"
                sent = len(run.steps)
                if run.status != "running":
                    yield f"event: done\ndata: {json.dumps({'status': run.status})}\n\n"
                    return
                time.sleep(0.2)
            yield 'event: done\ndata: {"status": "timeout"}\n\n'

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/runs/{run_id}/localize")
    def localize(body: LocalizeIn, request: Request, run_id: str = RunId) -> DiagnosisOut:
        run = load(run_id)
        if run.status == "running":
            raise ApiError(409, "run_not_finished", "The run is still in progress.")
        llm = None
        if body.method != "rule":
            guard_spend(request)
            llm = get_client()
        localizer = make_localizer(
            body.method, llm, settings.judge_model or "unset", settings.judge_temperature
        )
        try:
            diagnosis_id, diagnosis = diagnose(recorder, run, localizer)
        except OfflineMiss:
            raise ApiError(
                503, "offline_unavailable", "This diagnosis was not recorded for offline use."
            ) from None
        last = run.steps[-1].step_number if run.steps else 0
        return DiagnosisOut(
            diagnosis_id=diagnosis_id,
            method=diagnosis.method,
            predicted_step=diagnosis.predicted_step,
            failure_type=diagnosis.failure_type.value if diagnosis.failure_type else None,
            reason=diagnosis.reason,
            evidence=[e.model_dump() for e in diagnosis.evidence],  # type: ignore[misc]
            tokens=diagnosis.tokens,
            seconds=diagnosis.seconds,
            used_llm=diagnosis.used_llm,
            # Every later model call had the predicted step in its context.
            affected_steps=list(range(diagnosis.predicted_step + 1, last + 1))
            if diagnosis.predicted_step
            else [],
            injected_step=run.injected_step,
        )

    @app.post("/api/runs/{run_id}/resume", status_code=202)
    def resume(body: ResumeIn, request: Request, run_id: str = RunId) -> RunStarted:
        parent = load(run_id)
        if parent.status == "running":
            raise ApiError(409, "run_not_finished", "The run is still in progress.")
        if body.step not in {s.step_number for s in parent.steps}:
            raise ApiError(422, "invalid_request", f"The run has no step {body.step}.")
        forced: ToolCall | None = None
        hint: str | None = None
        if body.mode == "forced_action":
            if body.tool_name is None or body.arguments is None:
                raise ApiError(422, "invalid_request", "A forced action needs tool_name and arguments.")
            # The edited call is checked against the tool's schema before
            # anything runs. No free text from the request reaches the model.
            errors = argument_errors(body.tool_name, body.arguments)
            if errors:
                raise ApiError(422, "invalid_arguments", "; ".join(errors))
            forced = ToolCall(tool_name=body.tool_name, arguments=body.arguments)
        else:
            failure_type: FailureType | None = None
            if body.diagnosis_id:
                rows = recorder.query(
                    "SELECT * FROM diagnoses WHERE id = ? AND run_id = ?", (body.diagnosis_id, run_id)
                )
                if not rows:
                    raise ApiError(404, "diagnosis_not_found", "No such diagnosis for this run.")
                failure_type = load_diagnosis(rows[0]).failure_type
            hint = build_repair_hint(parent.step(body.step), failure_type)
        guard_spend(request)
        llm = get_client()
        new_id = start_continuation(
            recorder, parent, body.step, body.mode, agent_settings, hint=hint, purpose="visitor"
        )
        launch(new_id, lambda: run_continuation(llm, recorder, new_id, agent_settings, forced))
        return RunStarted(run_id=new_id)

    @app.post("/api/runs/{run_id}/restart", status_code=202)
    def restart(request: Request, run_id: str = RunId) -> RunStarted:
        parent = load(run_id)
        if parent.status == "running":
            raise ApiError(409, "run_not_finished", "The run is still in progress.")
        guard_spend(request)
        llm = get_client()
        new_id = start_full_restart(recorder, parent, agent_settings, purpose="visitor")
        launch(new_id, lambda: run_full_restart(llm, recorder, new_id, agent_settings))
        return RunStarted(run_id=new_id)

    @app.get("/api/runs/{run_id}/compare/{other_id}")
    def compare(run_id: str = RunId, other_id: str = Path(pattern=RUN_ID_PATTERN)) -> CompareOut:
        original, other = load(run_id), load(other_id)
        if other.parent_run_id != original.id:
            raise ApiError(422, "invalid_request", "The second run is not a recovery of the first.")
        # Every diagnosis made on the failed run was paid for on the way to this
        # repair, so all of them count as localization cost.
        localization = int(
            recorder.query(
                "SELECT COALESCE(SUM(tokens), 0) AS n FROM diagnoses WHERE run_id = ?", (original.id,)
            )[0]["n"]
        )
        restarts = [
            r
            for r in recorder.find_runs(parent_run_id=original.id, kind="full_restart")
            if r.status != "error"
        ]
        return CompareOut(
            original=run_cost(original),
            other=run_cost(other, localization if other.kind == "continuation" else 0),
            restart=run_cost(recorder.load_run(restarts[-1].id)) if restarts else None,
        )

    @app.get("/api/results")
    def results() -> JSONResponse:
        """The frozen summary file exactly as stored. Never recomputed here."""
        path = settings.results_dir / "frozen" / "results.json"
        if not path.is_file():
            raise ApiError(404, "no_results", "The experiment has not been run and frozen yet.")
        return JSONResponse(content=json.loads(path.read_text()))

    @app.post("/api/reset")
    def reset(request: Request) -> ResetOut:
        """Clear visitor runs and return to a clean demo. Experiment data stays."""
        rate_limit(request)
        ids = [
            r["id"]
            for r in recorder.query("SELECT id FROM runs WHERE purpose = 'visitor' AND status != 'running'")
        ]
        for run_id in ids:
            recorder.execute("DELETE FROM diagnoses WHERE run_id = ?", (run_id,))
            recorder.execute("DELETE FROM steps WHERE run_id = ?", (run_id,))
            recorder.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        return ResetOut(runs_cleared=len(ids))

    @app.api_route(
        "/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"], include_in_schema=False
    )
    def unknown_api(rest: str) -> None:
        raise ApiError(404, "not_found", "No such endpoint.")

    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    return app
