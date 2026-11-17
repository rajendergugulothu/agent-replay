"""Counterfactual labeling: is the injected step really the cause?

For each scenario the injected step alone is fixed and the agent continues,
several times. The label is confirmed only if success comes back. Scenarios
that do not recover, or never fail, are flagged and kept.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_replay.injection import Scenario, scenario_path, scenario_sha256
from agent_replay.planner.agent import AgentSettings
from agent_replay.recovery import continue_run, original_action, resume_at_true_step, run_scenario
from agent_replay.tracing import TraceRecorder, now


def sync_scenarios(recorder: TraceRecorder, scenarios: list[Scenario], scenarios_dir: Path) -> None:
    """Mirror the YAML files into the scenarios table. An edited file loses its
    label, because the label was earned by the old definition."""
    for scenario in scenarios:
        sha = scenario_sha256(scenario_path(scenarios_dir, scenario.id))
        rows = recorder.query("SELECT yaml_sha256 FROM scenarios WHERE id = ?", (scenario.id,))
        if rows and rows[0]["yaml_sha256"] == sha:
            continue
        recorder.execute(
            """INSERT OR REPLACE INTO scenarios
                   (id, title, failure_type, yaml_sha256, definition_json, label_status)
               VALUES (?, ?, ?, ?, ?, 'unlabeled')""",
            (scenario.id, scenario.title, scenario.failure_type.value, sha, scenario.model_dump_json()),
        )


def label_scenario(
    client: Any,
    recorder: TraceRecorder,
    scenario: Scenario,
    settings: AgentSettings,
    trials: int = 3,
) -> dict[str, Any]:
    original = recorder.load_run(run_scenario(client, recorder, scenario, settings, purpose="labeling"))
    detail: dict[str, Any] = {"original_status": original.status, "fixes": []}
    passes = 0
    if original.status == "passed":
        status = "no_failure"
    elif original.injected_step is None:
        status = "fault_not_delivered"
    else:
        for _ in range(trials):
            fixed = recorder.load_run(
                resume_at_true_step(client, recorder, original, settings, purpose="labeling")
            )
            passes += fixed.status == "passed"
        # Confirmed when most continuations recover: 2 of the default 3.
        status = "confirmed" if passes * 2 > trials else "not_recovered"
        detail["fixes"].append({"step": original.injected_step, "passes": passes, "trials": trials})
        # Two-error scenarios: also try fixing each later fault by itself, with
        # the earlier fault still in the history. This answers whether the
        # first injected step is the only cause.
        later = [s for s in original.steps if s.injection and s.step_number > original.injected_step]
        for step in later:
            action = original_action(step)
            if action is None:
                continue
            alone = 0
            for _ in range(trials):
                run_id = continue_run(
                    client,
                    recorder,
                    parent_run_id=original.id,
                    step=step.step_number,
                    mode="forced_action",
                    settings=settings,
                    forced_action=action,
                    purpose="labeling",
                )
                alone += recorder.load_run(run_id).status == "passed"
            detail["fixes"].append({"step": step.step_number, "passes": alone, "trials": trials})

    recorder.execute(
        """UPDATE scenarios SET label_status = ?, counterfactual_trials = ?,
               counterfactual_passes = ?, label_source_run_id = ?, label_detail_json = ?,
               labeled_at = ? WHERE id = ?""",
        (status, trials, passes, original.id, json.dumps(detail), now(), scenario.id),
    )
    return {"scenario_id": scenario.id, "label_status": status, "passes": passes, "trials": trials}


def label_all(
    client: Any,
    recorder: TraceRecorder,
    scenarios: list[Scenario],
    scenarios_dir: Path,
    settings: AgentSettings,
    trials: int = 3,
    log: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    sync_scenarios(recorder, scenarios, scenarios_dir)
    results = []
    for scenario in scenarios:
        row = recorder.query("SELECT label_status FROM scenarios WHERE id = ?", (scenario.id,))[0]
        if row["label_status"] != "unlabeled":
            log(f"{scenario.id}: already labeled {row['label_status']}, skipped")
            continue
        result = label_scenario(client, recorder, scenario, settings, trials)
        log(f"{scenario.id}: {result['label_status']} ({result['passes']}/{trials} recovered)")
        results.append(result)
    return results
