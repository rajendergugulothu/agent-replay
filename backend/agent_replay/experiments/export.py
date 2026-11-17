"""Turn stored runs into the results summary (JSON and CSV).

Every number here is computed from rows in the store, and every group lists
the run ids behind it.
"""

import csv
import json
from pathlib import Path
from typing import Any

from agent_replay.experiments.runner import METHODS
from agent_replay.experiments.scoring import ScoredDiagnosis, ratio, score_all
from agent_replay.tracing import TraceRecorder, now


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _true_type(recorder: TraceRecorder, run_id: str, step: int) -> str:
    row = recorder.query(
        "SELECT injection_json FROM steps WHERE run_id = ? AND step_number = ?", (run_id, step)
    )[0]
    return str(json.loads(row["injection_json"])[0]["failure_type"])


def build_summary(recorder: TraceRecorder, experiment_id: str) -> dict[str, Any]:
    originals = recorder.query(
        """SELECT * FROM runs WHERE experiment_id = ? AND kind = 'original'
           AND status IN ('passed', 'failed') ORDER BY scenario_id, trial""",
        (experiment_id,),
    )
    labels = {row["id"]: row for row in recorder.query("SELECT * FROM scenarios")}
    all_runs = recorder.query("SELECT id, status, model FROM runs WHERE experiment_id = ?", (experiment_id,))
    recoveries = recorder.query("SELECT * FROM recoveries WHERE experiment_id = ?", (experiment_id,))
    by_original: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for rec in recoveries:
        by_original.setdefault(rec["original_run_id"], {})[(rec["strategy"], rec["method"])] = rec

    scenarios: dict[str, dict[str, Any]] = {}
    scored: list[ScoredDiagnosis] = []
    missed: list[dict[str, Any]] = []
    offsets: dict[int, dict[str, Any]] = {}
    verdicts: dict[tuple[str, str], list[tuple[int | None, str | None]]] = {}

    for run in originals:
        sid = run["scenario_id"]
        label = labels.get(sid, {})
        info = scenarios.setdefault(
            sid,
            {
                "id": sid,
                "title": label.get("title", sid),
                "failure_type": label.get("failure_type"),
                "label_status": label.get("label_status", "unlabeled"),
                "counterfactual_passes": label.get("counterfactual_passes"),
                "counterfactual_trials": label.get("counterfactual_trials"),
                "original_runs": 0,
                "failed": 0,
                "no_failure": 0,
                "fault_not_delivered": 0,
            },
        )
        info["original_runs"] += 1
        if run["status"] == "passed":
            info["no_failure"] += 1
            continue
        info["failed"] += 1
        if run["injected_step"] is None:
            info["fault_not_delivered"] += 1
            continue
        # Localization is only scored where the label was confirmed: the
        # injected step counts as the true step only if fixing it brings
        # success back.
        if info["label_status"] != "confirmed":
            continue
        true_step = run["injected_step"]
        true_type = _true_type(recorder, run["id"], true_step)
        for diag in recorder.query("SELECT * FROM diagnoses WHERE run_id = ?", (run["id"],)):
            method = diag["method"]
            scored.append(
                ScoredDiagnosis(
                    method=method,
                    predicted_step=diag["predicted_step"],
                    true_step=true_step,
                    predicted_type=diag["failure_type"],
                    true_type=true_type,
                    tokens=diag["tokens"],
                    seconds=diag["seconds"],
                    run_id=run["id"],
                )
            )
            offset = None if diag["predicted_step"] is None else diag["predicted_step"] - true_step
            verdicts.setdefault((sid, method), []).append((offset, diag["failure_type"]))
            recovery = by_original.get(run["id"], {}).get(("resume_predicted", method))
            recovered = bool(recovery and recovery["task_success"])
            if offset is not None and recovery:
                bucket = offsets.setdefault(offset, {"offset": offset, "n": 0, "successes": 0, "run_ids": []})
                bucket["n"] += 1
                bucket["successes"] += recovered
                bucket["run_ids"].append(recovery["recovery_run_id"])
            if diag["predicted_step"] is None:
                why = "no_finding"
            elif diag["predicted_step"] != true_step:
                why = "wrong_step"
            elif diag["failure_type"] != true_type:
                why = "right_step_wrong_type"
            elif not recovered:
                why = "right_step_not_recovered"
            else:
                continue
            missed.append(
                {
                    "scenario_id": sid,
                    "trial": run["trial"],
                    "method": method,
                    "why": why,
                    "true_step": true_step,
                    "predicted_step": diag["predicted_step"],
                    "true_type": true_type,
                    "predicted_type": diag["failure_type"],
                    "localizer_reason": diag["reason"],
                    "recovered": recovered,
                    "original_run_id": run["id"],
                    "recovery_run_id": recovery["recovery_run_id"] if recovery else None,
                }
            )

    by_localizer: dict[str, Any] = {}
    for method, score in score_all(scored).items():
        entry = score.model_dump()
        groups = [v for (_, m), v in verdicts.items() if m == method]
        # Consistent means the same verdict (distance from the true step, and
        # type) in every trial of a scenario.
        entry["consistency"] = ratio(sum(len(set(v)) == 1 for v in groups), len(groups)).model_dump()
        by_localizer[method] = entry

    by_strategy: dict[str, Any] = {}
    keys = [("full_restart", ""), ("resume_true", ""), *(("resume_predicted", m) for m in METHODS)]
    for strategy, method in keys:
        rows = [r for r in recoveries if r["strategy"] == strategy and r["method"] == method]
        if not rows:
            continue
        # A localizer with no finding has no recovery run. Counting it as a
        # zero-cost recovery would make the method look cheap, so token and time
        # means cover only recoveries that were attempted, and a recovery only
        # counts as beating a restart if it also succeeded.
        attempted = [r for r in rows if r["recovery_run_id"]]
        restart_tokens = []
        recovered_and_cheaper = 0
        for r in attempted:
            restart = by_original.get(r["original_run_id"], {}).get(("full_restart", ""))
            if restart:
                restart_tokens.append(float(restart["total_tokens"]))
                recovered_and_cheaper += (
                    bool(r["task_success"]) and r["total_tokens"] < restart["total_tokens"]
                )
        by_strategy[f"{strategy}:{method}" if method else strategy] = {
            "strategy": strategy,
            "method": method or None,
            "n": len(rows),
            "attempted": ratio(len(attempted), len(rows)).model_dump(),
            "recovery_success": ratio(sum(r["task_success"] for r in rows), len(rows)).model_dump(),
            "recovered_and_cheaper": ratio(recovered_and_cheaper, len(rows)).model_dump(),
            "mean_localization_tokens": _mean([r["localization_tokens"] for r in attempted]),
            "mean_repair_tokens": _mean([r["repair_tokens"] for r in attempted]),
            "mean_replay_tokens": _mean([r["replay_tokens"] for r in attempted]),
            "mean_total_tokens": _mean([r["total_tokens"] for r in attempted]),
            "mean_seconds": _mean([r["seconds"] for r in attempted]),
            "mean_restart_tokens": _mean(restart_tokens),
            "run_ids": [r["recovery_run_id"] for r in attempted],
        }

    models = sorted({r["model"] for r in all_runs})
    judge_models = sorted(
        {
            d["judge_model"]
            for d in recorder.query(
                """SELECT DISTINCT judge_model FROM diagnoses JOIN runs ON runs.id = diagnoses.run_id
                   WHERE runs.experiment_id = ? AND judge_model IS NOT NULL""",
                (experiment_id,),
            )
        }
    )
    return {
        "experiment_id": experiment_id,
        "computed_at": now(),
        "agent_models": models,
        "judge_models": judge_models,
        "run_count": len(all_runs),
        "runs_errored": sum(r["status"] == "error" for r in all_runs),
        "scenario_count": len(scenarios),
        "scenarios": list(scenarios.values()),
        "by_localizer": by_localizer,
        "by_strategy": by_strategy,
        "recovery_by_offset": [offsets[k] for k in sorted(offsets)],
        "missed_cases": missed,
    }


def _metric_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for kind, groups in (("localizer", summary["by_localizer"]), ("strategy", summary["by_strategy"])):
        for key, group in groups.items():
            for metric, value in group.items():
                if metric in {"method", "strategy", "run_ids"}:
                    continue
                row = {"group_kind": kind, "group_key": key, "metric": metric, "run_ids": group["run_ids"]}
                if isinstance(value, dict):
                    row.update(
                        value=value["value"], numerator=value["numerator"], denominator=value["denominator"]
                    )
                else:
                    row.update(value=value, numerator=None, denominator=None)
                rows.append(row)
    return rows


def export_results(recorder: TraceRecorder, experiment_id: str, out_dir: Path) -> dict[str, Any]:
    """Write results.json and results.csv and mirror the metrics into the
    experiment_results table."""
    summary = build_summary(recorder, experiment_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(summary, indent=2))
    rows = _metric_rows(summary)
    with (out_dir / "results.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["group_kind", "group_key", "metric", "value", "numerator", "denominator"])
        for row in rows:
            writer.writerow(
                [
                    row["group_kind"],
                    row["group_key"],
                    row["metric"],
                    row["value"],
                    row["numerator"],
                    row["denominator"],
                ]
            )
    for row in rows:
        recorder.insert(
            "experiment_results",
            {
                "experiment_id": experiment_id,
                "group_kind": row["group_kind"],
                "group_key": row["group_key"],
                "metric": row["metric"],
                "value": row["value"],
                "numerator": row["numerator"],
                "denominator": row["denominator"],
                "run_ids_json": json.dumps(row["run_ids"]),
                "computed_at": summary["computed_at"],
            },
        )
    return summary


def markdown_summary(summary: dict[str, Any]) -> str:
    """The results as Markdown tables, for the README. Built only from the
    summary file, so the README cannot drift from the stored numbers."""

    def pct(r: dict[str, Any]) -> str:
        return "n/a" if r["value"] is None else f"{r['value']:.0%} ({r['numerator']}/{r['denominator']})"

    def num(value: float | None) -> str:
        return "n/a" if value is None else f"{value:,.0f}"

    frozen = summary.get("frozen", {})
    lines = [
        f"Experiment `{summary['experiment_id']}`"
        + (f", version {frozen['version']}, frozen on {frozen['frozen_on']}" if frozen else "")
        + f". {summary['run_count']} stored runs across {summary['scenario_count']} scenarios. "
        f"Agent model: {', '.join(summary['agent_models']) or 'n/a'}. "
        f"Judge model: {', '.join(summary['judge_models']) or 'n/a'}.",
        "",
        "| Localizer | Scored runs | Exact step | Within one step | Failure type | Mean tokens |",
        "|---|---|---|---|---|---|",
    ]
    for method, g in summary["by_localizer"].items():
        lines.append(
            f"| {method} | {g['n']} | {pct(g['exact_step_accuracy'])} | {pct(g['within_one_accuracy'])} "
            f"| {pct(g['failure_type_accuracy'])} | {num(g['mean_tokens'])} |"
        )
    lines += [
        "",
        "| Recovery strategy | Cases | Attempted | Recovered | Recovered and cheaper than a restart | Mean tokens when attempted | Mean restart tokens |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, g in summary["by_strategy"].items():
        lines.append(
            f"| {key} | {g['n']} | {pct(g['attempted'])} | {pct(g['recovery_success'])} "
            f"| {pct(g['recovered_and_cheaper'])} | {num(g['mean_total_tokens'])} | {num(g['mean_restart_tokens'])} |"
        )
    unconfirmed = [s["id"] for s in summary["scenarios"] if s["label_status"] != "confirmed"]
    lines += ["", f"Missed cases listed in the results file: {len(summary['missed_cases'])}."]
    if unconfirmed:
        lines.append(
            f"Scenarios without a confirmed label, excluded from localization scoring: {', '.join(unconfirmed)}."
        )
    return "\n".join(lines)
