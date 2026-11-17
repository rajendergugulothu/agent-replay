"""The experiment runner.

Per scenario and trial: one original failed run, a diagnosis from each
localizer, one full restart and one resume at the true step (both shared by all
localizers), and one resume at the predicted step per distinct prediction.
Every finished piece is found in the store on a rerun and skipped.
"""

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from agent_replay.experiments.cost import recovery_cost
from agent_replay.injection import Scenario
from agent_replay.localizers import Diagnosis, diagnose, load_diagnosis, make_localizer
from agent_replay.models import StoredRun
from agent_replay.planner.agent import AgentSettings
from agent_replay.recovery import (
    build_repair_hint,
    continue_run,
    full_restart,
    resume_at_true_step,
    run_scenario,
)
from agent_replay.tracing import TraceRecorder

METHODS = ("rule", "llm", "hybrid")
FINAL = ("passed", "failed")
# Used for the budget estimate only when the store has no finished runs yet.
ASSUMED_TOKENS_PER_RUN = 15_000
ASSUMED_TOKENS_PER_JUDGE_CALL = 3_000


class Plan(BaseModel):
    scenarios: int
    trials: int
    original_runs: int
    full_restarts: int
    resume_true_runs: int
    resume_predicted_runs_max: int
    agent_runs_max: int
    diagnoses: int
    judge_calls_max: int
    estimated_tokens_max: int
    estimate_basis: str


def make_plan(recorder: TraceRecorder, scenario_count: int, trials: int) -> Plan:
    cells = scenario_count * trials
    stored = recorder.query(
        """SELECT AVG(prompt_tokens + completion_tokens) AS mean, COUNT(*) AS n FROM runs
           WHERE status IN ('passed', 'failed') AND kind = 'original'"""
    )[0]
    judge = recorder.query("SELECT AVG(tokens) AS mean, COUNT(*) AS n FROM diagnoses WHERE used_llm = 1")[0]
    per_run = stored["mean"] or ASSUMED_TOKENS_PER_RUN
    per_judge = judge["mean"] or ASSUMED_TOKENS_PER_JUDGE_CALL
    basis = (
        f"mean of {stored['n']} stored original runs"
        if stored["n"]
        else f"assumed {ASSUMED_TOKENS_PER_RUN} tokens per run"
    ) + (
        f", mean of {judge['n']} stored judge calls"
        if judge["n"]
        else f", assumed {ASSUMED_TOKENS_PER_JUDGE_CALL} tokens per judge call"
    )
    agent_runs = cells * 6
    return Plan(
        scenarios=scenario_count,
        trials=trials,
        original_runs=cells,
        full_restarts=cells,
        resume_true_runs=cells,
        resume_predicted_runs_max=cells * 3,
        agent_runs_max=agent_runs,
        diagnoses=cells * 3,
        judge_calls_max=cells * 2,
        estimated_tokens_max=int(agent_runs * per_run + cells * 2 * per_judge),
        estimate_basis=basis,
    )


def _finished(runs: list[StoredRun]) -> StoredRun | None:
    done = [r for r in runs if r.status in FINAL]
    return done[0] if done else None


def _recovery_exists(recorder: TraceRecorder, original_id: str, strategy: str, method: str) -> bool:
    return bool(
        recorder.query(
            "SELECT 1 FROM recoveries WHERE original_run_id = ? AND strategy = ? AND method = ?",
            (original_id, strategy, method),
        )
    )


def _store_recovery(
    recorder: TraceRecorder,
    original: StoredRun,
    strategy: str,
    method: str,
    recovery_run: StoredRun | None,
    *,
    localization_tokens: int = 0,
    localization_seconds: float = 0.0,
    diagnosis_id: str | None = None,
) -> None:
    cost = recovery_cost(recovery_run, localization_tokens)
    recorder.insert(
        "recoveries",
        {
            "experiment_id": original.experiment_id,
            "scenario_id": original.scenario_id,
            "trial": original.trial,
            "original_run_id": original.id,
            "strategy": strategy,
            "method": method,
            "diagnosis_id": diagnosis_id,
            "recovery_run_id": recovery_run.id if recovery_run else None,
            "resume_step": recovery_run.branch_step if recovery_run else None,
            "task_success": int(recovery_run is not None and recovery_run.status == "passed"),
            "localization_tokens": cost.localization_tokens,
            "repair_tokens": cost.repair_tokens,
            "replay_tokens": cost.replay_tokens,
            "total_tokens": cost.total_tokens,
            "seconds": localization_seconds + (recovery_run.total_seconds if recovery_run else 0.0),
        },
    )


def run_cell(
    client: Any,
    recorder: TraceRecorder,
    scenario: Scenario,
    trial: int,
    experiment_id: str,
    settings: AgentSettings,
    judge_model: str,
    judge_temperature: float | None,
    log: Callable[[str], None],
    max_completion_tokens: int = 4_000,
) -> None:
    """Everything for one scenario and trial."""
    tag = f"{scenario.id} trial {trial}"
    found = recorder.find_runs(
        experiment_id=experiment_id, scenario_id=scenario.id, trial=trial, kind="original"
    )
    existing = _finished(found)
    if existing:
        original = recorder.load_run(existing.id)
    else:
        run_id = run_scenario(
            client,
            recorder,
            scenario,
            settings,
            purpose="experiment",
            experiment_id=experiment_id,
            trial=trial,
        )
        original = recorder.load_run(run_id)
        log(f"{tag}: original run {original.status}")

    if original.status == "passed":
        log(f"{tag}: the run passed despite the fault, no recovery to measure")
        return

    diagnoses: dict[str, tuple[str, Diagnosis]] = {}
    for method in METHODS:
        rows = recorder.query(
            "SELECT * FROM diagnoses WHERE run_id = ? AND method = ? ORDER BY created_at LIMIT 1",
            (original.id, method),
        )
        if rows:
            diagnoses[method] = (rows[0]["id"], load_diagnosis(rows[0]))
        else:
            localizer = make_localizer(method, client, judge_model, judge_temperature, max_completion_tokens)
            diagnoses[method] = diagnose(recorder, original, localizer)
            log(f"{tag}: {method} predicts step {diagnoses[method][1].predicted_step}")

    if not _recovery_exists(recorder, original.id, "full_restart", ""):
        done = _finished(recorder.find_runs(parent_run_id=original.id, kind="full_restart"))
        run_id = done.id if done else full_restart(client, recorder, original.id, settings)
        _store_recovery(recorder, original, "full_restart", "", recorder.load_run(run_id))

    if original.injected_step is None:
        log(f"{tag}: the fault was not delivered, no true step to resume at")
    elif not _recovery_exists(recorder, original.id, "resume_true", ""):
        done = _finished(
            [
                r
                for r in recorder.find_runs(parent_run_id=original.id, kind="continuation")
                if r.continuation_mode in ("forced_action", "regenerate")
            ]
        )
        run_id = done.id if done else resume_at_true_step(client, recorder, original, settings)
        _store_recovery(recorder, original, "resume_true", "", recorder.load_run(run_id))

    for method, (diagnosis_id, diagnosis) in diagnoses.items():
        if _recovery_exists(recorder, original.id, "resume_predicted", method):
            continue
        shared: StoredRun | None = None
        if diagnosis.predicted_step is not None:
            hint = build_repair_hint(original.step(diagnosis.predicted_step), diagnosis.failure_type)
            # Two localizers with the same step and type produce the same hint,
            # so the run one of them already made is exactly the run the other
            # would make.
            shared = _finished(
                [
                    r
                    for r in recorder.find_runs(
                        parent_run_id=original.id, kind="continuation", branch_step=diagnosis.predicted_step
                    )
                    if r.continuation_mode == "repair_hint" and r.repair_hint == hint
                ]
            )
            if shared is None:
                run_id = continue_run(
                    client,
                    recorder,
                    parent_run_id=original.id,
                    step=diagnosis.predicted_step,
                    mode="repair_hint",
                    settings=settings,
                    hint=hint,
                )
                shared = recorder.load_run(run_id)
            else:
                shared = recorder.load_run(shared.id)
        _store_recovery(
            recorder,
            original,
            "resume_predicted",
            method,
            shared,
            localization_tokens=diagnosis.tokens,
            localization_seconds=diagnosis.seconds,
            diagnosis_id=diagnosis_id,
        )
    log(f"{tag}: done")


def run_experiment(
    client: Any,
    recorder: TraceRecorder,
    scenarios: list[Scenario],
    settings: AgentSettings,
    *,
    experiment_id: str,
    judge_model: str,
    judge_temperature: float | None,
    trials: int = 3,
    max_completion_tokens: int = 4_000,
    log: Callable[[str], None] = print,
) -> list[str]:
    """Run every cell. Returns the cells that stopped on an error, so the
    caller can report them. Rerunning picks those cells up again."""
    stopped: list[str] = []
    # A run left 'running' by an interrupted attempt will never finish.
    recorder.execute(
        "UPDATE runs SET status = 'error', stop_reason = 'interrupted' WHERE status = 'running' AND experiment_id = ?",
        (experiment_id,),
    )
    for scenario in scenarios:
        for trial in range(1, trials + 1):
            try:
                run_cell(
                    client,
                    recorder,
                    scenario,
                    trial,
                    experiment_id,
                    settings,
                    judge_model,
                    judge_temperature,
                    log,
                    max_completion_tokens,
                )
            except Exception as exc:  # noqa: BLE001
                # One failed API call must not lose the rest of the experiment.
                # The cell is reported, never silently skipped.
                stopped.append(f"{scenario.id} trial {trial}")
                log(f"{scenario.id} trial {trial}: stopped, {type(exc).__name__}: {exc}")
    return stopped
