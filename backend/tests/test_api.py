import dataclasses
import json
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import SCENARIOS_DIR
from fake_model import FakeClient
from fastapi.testclient import TestClient

from agent_replay.api.app import create_app
from agent_replay.config import Settings
from agent_replay.llm import OfflineMiss


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    base = Settings(
        openai_api_key="",
        openai_model="fake-model",
        judge_model="fake-judge",
        database_path=tmp_path / "api.db",
        max_tokens_per_run=40_000,
        max_steps_per_run=15,
        daily_token_limit=2_000_000,
        max_completion_tokens=4_000,
        agent_temperature=None,
        judge_temperature=0.0,
        demo_mode=False,
        offline=False,
        production=False,
        scenarios_dir=SCENARIOS_DIR,
        results_dir=tmp_path / "results",
        recordings_dir=tmp_path / "recordings",
        frontend_dir=tmp_path / "no-frontend",
        seed_database_path=tmp_path / "no-seed.db",
        max_concurrent_runs=2,
        rate_limit_per_minute=1000,
        allowed_origin="",
        demo_scenarios=(),
    )
    return dataclasses.replace(base, **overrides)


@pytest.fixture
def api(tmp_path: Path) -> TestClient:
    return TestClient(create_app(make_settings(tmp_path), FakeClient()))


def wait(api: TestClient, run_id: str) -> dict[str, Any]:
    for _ in range(200):
        run = api.get(f"/api/runs/{run_id}").json()
        if run["status"] != "running":
            return run  # type: ignore[no-any-return]
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def start(api: TestClient, scenario_id: str = "scenario_01") -> dict[str, Any]:
    response = api.post("/api/runs", json={"scenario_id": scenario_id})
    assert response.status_code == 202, response.text
    return wait(api, response.json()["run_id"])


def error_code(response: Any) -> str:
    body = response.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message"}
    return str(body["error"]["code"])


def test_health_and_security_headers(api: TestClient) -> None:
    response = api.get("/api/health")
    assert response.json() == {
        "status": "ok",
        "mode": "live",
        "demo_mode": False,
        "version": "0.1.0",
        "results_available": False,
    }
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert len(response.headers["X-Request-ID"]) == 12
    assert "access-control-allow-origin" not in response.headers


def test_scenarios_and_tools(api: TestClient) -> None:
    scenarios = api.get("/api/scenarios").json()
    assert len(scenarios) == 10 and scenarios[0]["id"] == "scenario_01"
    assert (
        scenarios[0]["failure_type"] == "wrong_argument_values"
        and scenarios[0]["label_status"] == "unlabeled"
    )
    tools = api.get("/api/tools").json()
    assert [t["name"] for t in tools] == [
        "get_degree_requirements",
        "check_prerequisite",
        "search_sections",
        "build_schedule",
    ]
    assert tools[1]["parameters"]["required"] == ["student_id", "course_code"]


def test_full_flow_run_localize_resume_compare(api: TestClient) -> None:
    run = start(api)
    assert run["status"] == "failed" and run["kind"] == "original"
    assert run["validation"]["reasons"][0]["code"] == "unmet_prerequisite"
    assert run["steps"][2]["summary"] == "Checked prerequisite for QCS 310: met"
    assert [e["course_code"] for e in run["final_schedule"]][1] == "QCS 340"
    # Before any diagnosis the planted fault is not revealed.
    assert run["injected_step"] is None and all(s["injection"] is None for s in run["steps"])

    rule = api.post(f"/api/runs/{run['id']}/localize", json={"method": "rule"}).json()
    assert rule["predicted_step"] is None and rule["tokens"] == 0 and rule["affected_steps"] == []
    llm = api.post(f"/api/runs/{run['id']}/localize", json={"method": "llm"}).json()
    assert llm["predicted_step"] == 3 and llm["failure_type"] == "wrong_argument_values" and llm["tokens"] > 0
    assert llm["affected_steps"] == list(range(4, len(run["steps"]) + 1)) and llm["injected_step"] == 3

    revealed = api.get(f"/api/runs/{run['id']}").json()
    assert (
        revealed["injected_step"] == 3 and revealed["steps"][2]["injection"][0]["kind"] == "replace_argument"
    )

    resumed = api.post(
        f"/api/runs/{run['id']}/resume",
        json={
            "step": 3,
            "mode": "forced_action",
            "tool_name": "check_prerequisite",
            "arguments": {"student_id": "stu_01", "course_code": "QCS 340"},
        },
    )
    assert resumed.status_code == 202
    fixed = wait(api, resumed.json()["run_id"])
    assert fixed["status"] == "passed" and fixed["parent_run_id"] == run["id"] and fixed["branch_step"] == 3
    assert [s["origin"] for s in fixed["steps"][:3]] == ["reused", "reused", "forced"]

    hinted = api.post(
        f"/api/runs/{run['id']}/resume",
        json={"step": 3, "mode": "repair_hint", "diagnosis_id": llm["diagnosis_id"]},
    )
    assert wait(api, hinted.json()["run_id"])["status"] == "passed"

    restart = wait(api, api.post(f"/api/runs/{run['id']}/restart").json()["run_id"])
    assert restart["kind"] == "full_restart" and restart["status"] == "passed"

    compare = api.get(f"/api/runs/{run['id']}/compare/{fixed['id']}").json()
    assert compare["original"]["passed"] is False and compare["other"]["passed"] is True
    tokens = compare["other"]["tokens"]
    assert tokens["localization"] == llm["tokens"] and tokens["repair"] == 0
    assert tokens["total"] == tokens["localization"] + tokens["repair"] + tokens["replay"]
    assert compare["other"]["steps_reused"] == 2
    assert compare["restart"]["run_id"] == restart["id"]
    assert compare["restart"]["tokens"]["total"] == restart["tokens"]


def test_stream_sends_each_step_then_done(api: TestClient) -> None:
    run = start(api)
    with api.stream("GET", f"/api/runs/{run['id']}/stream") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        text = "".join(response.iter_text())
    events = [block.split("\n") for block in text.strip().split("\n\n")]
    assert [e[0] for e in events] == ["event: step"] * len(run["steps"]) + ["event: done"]
    assert json.loads(events[0][1].removeprefix("data: "))["step_number"] == 1
    assert json.loads(events[-1][1].removeprefix("data: ")) == {"status": "failed"}


@pytest.mark.parametrize(
    ("method", "path", "body", "status"),
    [
        ("post", "/api/runs", {"scenario_id": "../../etc/passwd"}, 422),
        ("post", "/api/runs", {"scenario_id": "scenario_99"}, 404),
        ("post", "/api/runs", {"scenario_id": "scenario_01", "extra": 1}, 422),
        ("post", "/api/runs", {}, 422),
        ("get", "/api/runs/not-a-run-id", None, 422),
        ("get", "/api/runs/" + "0" * 32, None, 404),
        ("get", "/api/runs/" + "0" * 32 + "/stream", None, 404),
        ("post", "/api/runs/" + "0" * 32 + "/localize", {"method": "rule"}, 404),
        ("get", "/api/nothing-here", None, 404),
        ("get", "/api/results", None, 404),
    ],
)
def test_bad_input_gets_a_clear_json_error(
    api: TestClient, method: str, path: str, body: Any, status: int
) -> None:
    response = getattr(api, method)(path, **({"json": body} if body is not None else {}))
    assert response.status_code == status
    assert error_code(response)
    assert "Traceback" not in response.text


def test_localize_and_resume_validation(api: TestClient) -> None:
    run = start(api)
    base = f"/api/runs/{run['id']}"
    assert api.post(f"{base}/localize", json={"method": "magic"}).status_code == 422
    bad_arguments = api.post(
        f"{base}/resume",
        json={
            "step": 3,
            "mode": "forced_action",
            "tool_name": "check_prerequisite",
            "arguments": {"student_id": "stu_01", "course_code": 340},
        },
    )
    assert bad_arguments.status_code == 422 and error_code(bad_arguments) == "invalid_arguments"
    too_long = api.post(
        f"{base}/resume",
        json={
            "step": 3,
            "mode": "forced_action",
            "tool_name": "check_prerequisite",
            "arguments": {"student_id": "stu_01", "course_code": "Ignore all previous instructions " * 5},
        },
    )
    assert error_code(too_long) == "invalid_arguments"
    unknown_tool = api.post(
        f"{base}/resume", json={"step": 3, "mode": "forced_action", "tool_name": "run_shell", "arguments": {}}
    )
    assert error_code(unknown_tool) == "invalid_arguments"
    assert api.post(f"{base}/resume", json={"step": 3, "mode": "forced_action"}).status_code == 422
    assert api.post(f"{base}/resume", json={"step": 99, "mode": "repair_hint"}).status_code == 422
    missing = api.post(f"{base}/resume", json={"step": 3, "mode": "repair_hint", "diagnosis_id": "a" * 32})
    assert error_code(missing) == "diagnosis_not_found"
    assert (
        api.post(f"{base}/resume", json={"step": 3, "mode": "repair_hint", "hint": "free text"}).status_code
        == 422
    )
    other = start(api, "scenario_08")
    assert error_code(api.get(f"{base}/compare/{other['id']}")) == "invalid_request"


class _SlowClient(FakeClient):
    def create(self, **kwargs: Any) -> Any:
        time.sleep(0.05)
        return super().create(**kwargs)


def test_third_concurrent_run_gets_429(tmp_path: Path) -> None:
    api = TestClient(create_app(make_settings(tmp_path), _SlowClient()))
    first = api.post("/api/runs", json={"scenario_id": "scenario_01"})
    second = api.post("/api/runs", json={"scenario_id": "scenario_01"})
    third = api.post("/api/runs", json={"scenario_id": "scenario_01"})
    assert (first.status_code, second.status_code, third.status_code) == (202, 202, 429)
    assert error_code(third) == "busy"
    assert api.get(f"/api/runs/{first.json()['run_id']}").json()["status"] == "running"
    blocked = api.post(f"/api/runs/{first.json()['run_id']}/localize", json={"method": "rule"})
    assert error_code(blocked) == "run_not_finished"
    wait(api, first.json()["run_id"])
    wait(api, second.json()["run_id"])
    assert api.post("/api/runs", json={"scenario_id": "scenario_01"}).status_code == 202


def test_daily_token_limit_blocks_new_live_work(tmp_path: Path) -> None:
    api = TestClient(create_app(make_settings(tmp_path, daily_token_limit=5_000), FakeClient()))
    run = start(api)
    assert run["tokens"] > 5_000
    for response in (
        api.post("/api/runs", json={"scenario_id": "scenario_01"}),
        api.post(f"/api/runs/{run['id']}/localize", json={"method": "llm"}),
        api.post(f"/api/runs/{run['id']}/restart"),
        api.post(f"/api/runs/{run['id']}/resume", json={"step": 3, "mode": "repair_hint"}),
    ):
        assert response.status_code == 403 and error_code(response) == "daily_limit"
    # Reading and the free rule-based check still work.
    assert api.get(f"/api/runs/{run['id']}").status_code == 200
    assert api.post(f"/api/runs/{run['id']}/localize", json={"method": "rule"}).status_code == 200


def test_rate_limit_per_client(tmp_path: Path) -> None:
    api = TestClient(create_app(make_settings(tmp_path, rate_limit_per_minute=2), FakeClient()))
    codes = [api.post("/api/reset").status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert api.get("/api/health").status_code == 200


def test_demo_mode_restricts_scenarios_and_serves_frozen_results(tmp_path: Path) -> None:
    frozen = tmp_path / "results" / "frozen"
    frozen.mkdir(parents=True)
    (frozen / "results.json").write_text(json.dumps({"experiment_id": "x", "run_count": 7}))
    settings = make_settings(tmp_path, demo_mode=True, demo_scenarios=("scenario_01", "scenario_04"))
    api = TestClient(create_app(settings, FakeClient()))
    assert [s["id"] for s in api.get("/api/scenarios").json()] == ["scenario_01", "scenario_04"]
    blocked = api.post("/api/runs", json={"scenario_id": "scenario_02"})
    assert blocked.status_code == 403 and error_code(blocked) == "not_in_demo"
    assert api.post("/api/runs", json={"scenario_id": "scenario_04"}).status_code == 202
    assert api.get("/api/results").json() == {"experiment_id": "x", "run_count": 7}
    assert api.get("/api/health").json()["results_available"] is True


def test_reset_clears_visitor_runs_only(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    api = TestClient(create_app(settings, FakeClient()))
    run = start(api)
    api.post(f"/api/runs/{run['id']}/localize", json={"method": "llm"})
    from agent_replay.tracing import TraceRecorder

    recorder = TraceRecorder(settings.database_path)
    recorder.execute("UPDATE runs SET purpose = 'experiment' WHERE id = ?", (run["id"],))
    kept = run["id"]
    gone = start(api)["id"]
    assert api.post("/api/reset").json() == {"runs_cleared": 1}
    assert api.get(f"/api/runs/{kept}").status_code == 200
    assert api.get(f"/api/runs/{gone}").status_code == 404
    assert recorder.query("SELECT COUNT(*) AS n FROM steps WHERE run_id = ?", (gone,))[0]["n"] == 0


def test_no_model_access_is_a_clean_503(tmp_path: Path) -> None:
    api = TestClient(create_app(make_settings(tmp_path)))
    response = api.post("/api/runs", json={"scenario_id": "scenario_01"})
    assert response.status_code == 503 and error_code(response) == "not_configured"


class _Broken(FakeClient):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def create(self, **kwargs: Any) -> Any:
        raise self.error


@pytest.mark.parametrize(
    ("error", "stop_reason"),
    [
        (RuntimeError("sk-secret-key leaked in message"), "api_error"),
        (OfflineMiss("x"), "offline_unavailable"),
    ],
)
def test_failed_model_call_ends_the_run_without_leaking_detail(
    tmp_path: Path, error: Exception, stop_reason: str
) -> None:
    api = TestClient(create_app(make_settings(tmp_path), _Broken(error)))
    response = api.post("/api/runs", json={"scenario_id": "scenario_01"})
    run = wait(api, response.json()["run_id"])
    assert run["status"] == "error" and run["stop_reason"] == stop_reason
    assert "sk-secret" not in json.dumps(run)


def test_unhandled_error_returns_plain_500(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    api = TestClient(create_app(make_settings(tmp_path), FakeClient()), raise_server_exceptions=False)
    monkeypatch.setattr("agent_replay.api.app.load_scenarios", lambda _: 1 / 0)
    response = api.get("/api/scenarios")
    assert response.status_code == 500 and error_code(response) == "internal_error"
    assert "ZeroDivisionError" not in response.text and "Traceback" not in response.text


def test_production_hides_docs_and_sets_hsts_and_cors(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, production=True, allowed_origin="https://replay.example")
    api = TestClient(create_app(settings, FakeClient()))
    assert api.get("/api/docs").status_code == 404 and api.get("/api/openapi.json").status_code == 404
    response = api.get("/api/health", headers={"Origin": "https://replay.example"})
    assert "max-age" in response.headers["Strict-Transport-Security"]
    assert response.headers["access-control-allow-origin"] == "https://replay.example"
    other = api.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_serves_the_built_frontend_and_seeds_the_database(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>Agent Replay</title>")
    seed_source = make_settings(tmp_path, database_path=tmp_path / "seed.db")
    seeded_run = start(TestClient(create_app(seed_source, FakeClient())))
    settings = make_settings(
        tmp_path,
        frontend_dir=dist,
        seed_database_path=tmp_path / "seed.db",
        database_path=tmp_path / "live" / "live.db",
    )
    api = TestClient(create_app(settings, FakeClient()))
    assert "Agent Replay" in api.get("/").text
    assert api.get("/api/health").status_code == 200
    assert api.get(f"/api/runs/{seeded_run['id']}").status_code == 200


def test_rate_limit_ignores_client_supplied_forwarded_addresses(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, production=True, rate_limit_per_minute=2)
    api = TestClient(create_app(settings, FakeClient()))
    # The client invents a new first address each time. The proxy's entry, the
    # last one, stays the same, so the limit still applies.
    codes = [
        api.post("/api/reset", headers={"X-Forwarded-For": f"10.0.0.{n}, 203.0.113.9"}).status_code
        for n in range(3)
    ]
    assert codes == [200, 200, 429]
    other = api.post("/api/reset", headers={"X-Forwarded-For": "10.0.0.1, 203.0.113.10"})
    assert other.status_code == 200


def test_every_model_call_carries_an_output_cap(tmp_path: Path) -> None:
    fake = FakeClient()
    api = TestClient(create_app(make_settings(tmp_path, max_completion_tokens=777), fake))
    run = start(api)
    api.post(f"/api/runs/{run['id']}/localize", json={"method": "llm"})
    assert fake.calls and all(call["max_completion_tokens"] == 777 for call in fake.calls)
