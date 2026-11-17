"""Experiment runner, labeling, cost and export, including the research
integrity checks: shared baselines, cost totals, and traceable numbers."""

import csv
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import SCENARIOS_DIR
from fake_model import FakeClient

from agent_replay.experiments.cost import recovery_cost
from agent_replay.experiments.export import build_summary, export_results
from agent_replay.experiments.labeling import label_all, sync_scenarios
from agent_replay.experiments.runner import METHODS, make_plan, run_experiment
from agent_replay.injection import Scenario, load_scenario, load_scenarios
from agent_replay.planner.agent import AgentSettings
from agent_replay.recovery import replay_deterministic, resume_at_true_step, run_scenario
from agent_replay.tracing import TraceRecorder

SCENARIOS = [load_scenario(SCENARIOS_DIR / f"scenario_{n}.yaml") for n in ("01", "02", "08")]


def run_all(
    recorder: TraceRecorder, settings: AgentSettings, client: Any = None, trials: int = 2
) -> list[str]:
    return run_experiment(
        client or FakeClient(),
        recorder,
        SCENARIOS,
        settings,
        experiment_id="exp",
        judge_model="judge",
        judge_temperature=0.0,
        trials=trials,
        log=lambda _: None,
    )


@pytest.fixture
def done(recorder: TraceRecorder, agent_settings: AgentSettings) -> TraceRecorder:
    label_all(FakeClient(), recorder, SCENARIOS, SCENARIOS_DIR, agent_settings, log=lambda _: None)
    assert run_all(recorder, agent_settings) == []
    return recorder


def count(recorder: TraceRecorder, where: str) -> int:
    return int(
        recorder.query(f"SELECT COUNT(*) AS n FROM runs WHERE experiment_id = 'exp' AND {where}")[0]["n"]
    )


def test_plan_for_ten_scenarios_and_three_trials(recorder: TraceRecorder) -> None:
    plan = make_plan(recorder, 10, 3)
    assert (plan.original_runs, plan.full_restarts, plan.resume_true_runs) == (30, 30, 30)
    assert plan.resume_predicted_runs_max == 90 and plan.agent_runs_max == 180
    assert plan.diagnoses == 90 and plan.judge_calls_max == 60
    assert "assumed" in plan.estimate_basis and plan.estimated_tokens_max > 0


def test_shared_baselines_run_once_per_scenario_and_trial(done: TraceRecorder) -> None:
    # scenario_02 passes despite its fault under the fake model, so it has
    # originals but no recoveries. The other two fail in both trials.
    assert count(done, "kind = 'original'") == 6
    assert count(done, "kind = 'full_restart'") == 4
    assert count(done, "kind = 'continuation' AND continuation_mode = 'forced_action'") == 4
    rows = done.query("SELECT strategy, method, COUNT(*) AS n FROM recoveries GROUP BY strategy, method")
    counts = {(r["strategy"], r["method"]): r["n"] for r in rows}
    assert counts[("full_restart", "")] == 4 and counts[("resume_true", "")] == 4
    for method in METHODS:
        assert counts[("resume_predicted", method)] == 4
    # Never one baseline per localizer.
    per_original = done.query(
        "SELECT original_run_id, COUNT(DISTINCT recovery_run_id) AS n FROM recoveries "
        "WHERE strategy IN ('full_restart', 'resume_true') GROUP BY original_run_id"
    )
    assert all(r["n"] == 2 for r in per_original)


def test_localizers_with_the_same_prediction_share_one_resume_run(done: TraceRecorder) -> None:
    # On scenario_08 the rules find step 1, so hybrid returns the same step and
    # type and must reuse the rule localizer's run.
    rows = done.query(
        "SELECT method, recovery_run_id, localization_tokens FROM recoveries "
        "WHERE scenario_id = 'scenario_08' AND trial = 1 AND strategy = 'resume_predicted'"
    )
    by_method = {r["method"]: r for r in rows}
    assert by_method["rule"]["recovery_run_id"] == by_method["hybrid"]["recovery_run_id"]
    assert by_method["rule"]["localization_tokens"] == by_method["hybrid"]["localization_tokens"] == 0
    assert by_method["llm"]["localization_tokens"] > 0


def test_no_finding_gets_no_resume_run_and_counts_as_a_failed_recovery(done: TraceRecorder) -> None:
    row = done.query(
        "SELECT * FROM recoveries WHERE scenario_id = 'scenario_01' AND method = 'rule' "
        "AND strategy = 'resume_predicted' AND trial = 1"
    )[0]
    assert row["recovery_run_id"] is None and row["task_success"] == 0 and row["total_tokens"] == 0


def test_recovery_cost_always_equals_its_three_parts(done: TraceRecorder) -> None:
    rows = done.query("SELECT * FROM recoveries")
    assert rows
    for row in rows:
        assert row["total_tokens"] == row["localization_tokens"] + row["repair_tokens"] + row["replay_tokens"]
        if row["recovery_run_id"]:
            run = done.load_run(row["recovery_run_id"])
            # Repair plus replay is exactly what the recovery run spent.
            assert row["repair_tokens"] + row["replay_tokens"] == run.total_tokens
        if row["diagnosis_id"]:
            tokens = done.query("SELECT tokens FROM diagnoses WHERE id = ?", (row["diagnosis_id"],))[0][
                "tokens"
            ]
            assert row["localization_tokens"] == tokens


def test_cost_split_by_strategy(done: TraceRecorder, agent_settings: AgentSettings) -> None:
    by = {
        r["strategy"]: r
        for r in done.query(
            "SELECT * FROM recoveries WHERE scenario_id = 'scenario_01' AND trial = 1 AND method IN ('', 'llm')"
        )
    }
    # A forced action costs nothing to repair. A restart is all replay.
    assert by["resume_true"]["repair_tokens"] == 0 and by["resume_true"]["localization_tokens"] == 0
    assert by["full_restart"]["repair_tokens"] == 0 and by["full_restart"]["localization_tokens"] == 0
    assert by["resume_predicted"]["repair_tokens"] > 0 and by["resume_predicted"]["localization_tokens"] > 0
    assert recovery_cost(None, 50).total_tokens == 50


def test_rerunning_a_finished_experiment_adds_nothing(
    done: TraceRecorder, agent_settings: AgentSettings
) -> None:
    before = {
        t: done.query(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
        for t in ("runs", "steps", "diagnoses", "recoveries")
    }
    client = FakeClient()
    assert run_all(done, agent_settings, client) == []
    after = {t: done.query(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"] for t in before}
    assert after == before and client.calls == []


class _Flaky(FakeClient):
    """Fails once after a set number of calls, like a dropped connection."""

    def __init__(self, fail_at: int) -> None:
        super().__init__()
        self.fail_at = fail_at

    def create(self, **kwargs: Any) -> Any:
        if len(self.calls) == self.fail_at:
            self.calls.append({})
            raise ConnectionError("network dropped")
        return super().create(**kwargs)


def test_runner_resumes_after_an_interruption(recorder: TraceRecorder, agent_settings: AgentSettings) -> None:
    stopped = run_all(recorder, agent_settings, _Flaky(fail_at=40))
    assert len(stopped) == 1
    assert count(recorder, "status = 'error'") == 1
    assert run_all(recorder, agent_settings) == []

    reference = TraceRecorder(recorder.db_path.with_name("reference.db"))
    assert run_all(reference, agent_settings) == []
    for table in ("diagnoses", "recoveries"):
        n = recorder.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
        assert n == reference.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
    # The errored run stays in the store as a record. Everything else matches
    # an uninterrupted experiment: no duplicated originals or baselines.
    for where in ("kind = 'original' AND status != 'error'", "kind = 'full_restart' AND status != 'error'"):
        assert count(recorder, where) == int(
            reference.query(f"SELECT COUNT(*) AS n FROM runs WHERE {where}")[0]["n"]
        )
    unique = recorder.query(
        "SELECT COUNT(*) AS n FROM (SELECT 1 FROM runs WHERE kind = 'original' AND status != 'error' "
        "GROUP BY scenario_id, trial HAVING COUNT(*) > 1)"
    )[0]["n"]
    assert unique == 0


def test_labeling_confirms_flags_and_skips(recorder: TraceRecorder, agent_settings: AgentSettings) -> None:
    logs: list[str] = []
    label_all(FakeClient(), recorder, SCENARIOS, SCENARIOS_DIR, agent_settings, log=logs.append)
    labels = {r["id"]: r for r in recorder.query("SELECT * FROM scenarios")}
    assert labels["scenario_01"]["label_status"] == "confirmed"
    assert (
        labels["scenario_01"]["counterfactual_passes"],
        labels["scenario_01"]["counterfactual_trials"],
    ) == (3, 3)
    # A scenario whose fault does not cause a failure is flagged, not dropped.
    assert labels["scenario_02"]["label_status"] == "no_failure"
    assert recorder.load_run(labels["scenario_01"]["label_source_run_id"]).purpose == "labeling"

    client = FakeClient()
    label_all(client, recorder, SCENARIOS, SCENARIOS_DIR, agent_settings, log=logs.append)
    assert client.calls == [] and "already labeled" in logs[-1]


def test_two_error_scenario_records_each_fix(recorder: TraceRecorder, agent_settings: AgentSettings) -> None:
    scenario = load_scenario(SCENARIOS_DIR / "scenario_09.yaml")
    label_all(FakeClient(), recorder, [scenario], SCENARIOS_DIR, agent_settings, log=lambda _: None)
    detail = json.loads(recorder.query("SELECT label_detail_json FROM scenarios")[0]["label_detail_json"])
    assert len(detail["fixes"]) == 2 and detail["fixes"][0]["step"] < detail["fixes"][1]["step"]


def test_editing_a_scenario_file_clears_its_label(recorder: TraceRecorder, tmp_path: Path) -> None:
    path = tmp_path / "scenario_01.yaml"
    path.write_text((SCENARIOS_DIR / "scenario_01.yaml").read_text())
    scenario = load_scenario(path)
    sync_scenarios(recorder, [scenario], tmp_path)
    recorder.execute("UPDATE scenarios SET label_status = 'confirmed'")
    sync_scenarios(recorder, [scenario], tmp_path)
    assert recorder.query("SELECT label_status FROM scenarios")[0]["label_status"] == "confirmed"
    path.write_text(path.read_text().replace("QCS 310", "QCS 230"))
    sync_scenarios(recorder, [load_scenario(path)], tmp_path)
    assert recorder.query("SELECT label_status FROM scenarios")[0]["label_status"] == "unlabeled"


def test_summary_numbers_trace_back_to_stored_runs(done: TraceRecorder, tmp_path: Path) -> None:
    summary = export_results(done, "exp", tmp_path / "out")
    assert json.loads((tmp_path / "out" / "results.json").read_text()) == summary
    stored_ids = {r["id"] for r in done.query("SELECT id FROM runs")}

    # Only the confirmed scenarios are scored: scenario_01 and scenario_08, 2 trials each.
    for method in METHODS:
        group = summary["by_localizer"][method]
        assert group["n"] == 4 and set(group["run_ids"]) <= stored_ids
        exact = sum(
            1
            for run_id in group["run_ids"]
            if done.query(
                "SELECT predicted_step FROM diagnoses WHERE run_id = ? AND method = ?", (run_id, method)
            )[0]["predicted_step"]
            == done.load_run(run_id).injected_step
        )
        assert group["exact_step_accuracy"]["numerator"] == exact
    for key, group in summary["by_strategy"].items():
        assert set(group["run_ids"]) <= stored_ids, key
        passed = sum(done.load_run(run_id).status == "passed" for run_id in group["run_ids"])
        if group["strategy"] != "resume_predicted":
            assert group["recovery_success"]["numerator"] == passed
    assert summary["run_count"] == count(done, "1")

    scenario_02 = next(s for s in summary["scenarios"] if s["id"] == "scenario_02")
    assert scenario_02["no_failure"] == 2 and scenario_02["label_status"] == "no_failure"
    whys = {(m["scenario_id"], m["method"], m["why"]) for m in summary["missed_cases"]}
    assert ("scenario_01", "rule", "no_finding") in whys
    assert all(m["original_run_id"] in stored_ids for m in summary["missed_cases"])

    with (tmp_path / "out" / "results.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert {"group_kind", "group_key", "metric", "value"} <= set(rows[0])
    stored = done.query("SELECT COUNT(*) AS n FROM experiment_results WHERE experiment_id = 'exp'")[0]["n"]
    assert stored == len(rows)
    # Exporting twice replaces the rows instead of piling them up.
    export_results(done, "exp", tmp_path / "out")
    assert done.query("SELECT COUNT(*) AS n FROM experiment_results")[0]["n"] == stored


def test_empty_experiment_exports_empty_results(recorder: TraceRecorder) -> None:
    summary = build_summary(recorder, "nothing")
    assert summary["run_count"] == 0 and summary["by_localizer"] == {} and summary["missed_cases"] == []


def test_every_stored_experiment_run_replays_exactly(done: TraceRecorder) -> None:
    for row in done.query("SELECT id FROM runs"):
        result = replay_deterministic(done, row["id"])
        assert result.matches, (row["id"], result.mismatches)


def test_all_ten_scenarios_run_and_replay(recorder: TraceRecorder, agent_settings: AgentSettings) -> None:
    for scenario in load_scenarios(SCENARIOS_DIR):
        run = recorder.load_run(
            run_scenario(FakeClient(), recorder, scenario, agent_settings, purpose="test")
        )
        assert run.unfired_injections == 0, scenario.id
        assert replay_deterministic(recorder, run.id).matches
        if run.status == "failed":
            fixed = recorder.load_run(resume_at_true_step(FakeClient(), recorder, run, agent_settings))
            assert replay_deterministic(recorder, fixed.id).matches


def test_scenario_step_limit_is_applied(recorder: TraceRecorder, agent_settings: AgentSettings) -> None:
    scenario: Scenario = load_scenario(SCENARIOS_DIR / "scenario_06.yaml")
    run = recorder.load_run(run_scenario(FakeClient(), recorder, scenario, agent_settings, purpose="test"))
    assert len(run.steps) == 12 and run.stop_reason == "max_steps"


def test_experiment_passes_the_output_cap_to_agent_and_judge(
    recorder: TraceRecorder, agent_settings: AgentSettings
) -> None:
    client = FakeClient()
    run_experiment(
        client,
        recorder,
        SCENARIOS[:1],
        agent_settings,
        experiment_id="cap",
        judge_model="judge",
        judge_temperature=0.0,
        trials=1,
        max_completion_tokens=555,
        log=lambda _: None,
    )
    judge_calls = [c for c in client.calls if "response_format" in c]
    agent_calls = [c for c in client.calls if "response_format" not in c]
    assert judge_calls and all(c["max_completion_tokens"] == 555 for c in judge_calls)
    assert agent_calls and all(
        c["max_completion_tokens"] == agent_settings.max_completion_tokens for c in agent_calls
    )


def test_no_finding_cases_do_not_make_a_method_look_cheap(done: TraceRecorder) -> None:
    summary = build_summary(done, "exp")
    rule = summary["by_strategy"]["resume_predicted:rule"]
    # The rules find nothing on scenario_01 (2 trials) and find step 1 on scenario_08.
    assert (rule["n"], rule["attempted"]["numerator"]) == (4, 2)
    assert len(rule["run_ids"]) == 2
    attempted = done.query(
        "SELECT total_tokens FROM recoveries WHERE strategy = 'resume_predicted' AND method = 'rule' "
        "AND recovery_run_id IS NOT NULL"
    )
    assert rule["mean_total_tokens"] == sum(r["total_tokens"] for r in attempted) / 2
    assert rule["recovered_and_cheaper"]["denominator"] == 4
    assert rule["recovered_and_cheaper"]["numerator"] <= rule["recovery_success"]["numerator"]
