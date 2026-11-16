"""The three localizers and one function to run and store a diagnosis."""

import json
from typing import Any

from agent_replay.localizers.base import Diagnosis, Evidence, Localizer, LocalizerView, Method, view_of
from agent_replay.localizers.hybrid import HybridLocalizer
from agent_replay.localizers.llm_judge import LLMJudgeLocalizer
from agent_replay.localizers.rules import RuleBasedLocalizer
from agent_replay.models import StoredRun
from agent_replay.tracing import TraceRecorder, now

__all__ = [
    "Diagnosis",
    "Evidence",
    "HybridLocalizer",
    "LLMJudgeLocalizer",
    "Localizer",
    "LocalizerView",
    "Method",
    "RuleBasedLocalizer",
    "diagnose",
    "load_diagnosis",
    "make_localizer",
    "view_of",
]


def make_localizer(method: str, client: Any, judge_model: str, judge_temperature: float | None) -> Localizer:
    if method == "rule":
        return RuleBasedLocalizer()
    judge = LLMJudgeLocalizer(client, judge_model, judge_temperature)
    if method == "llm":
        return judge
    if method == "hybrid":
        return HybridLocalizer(judge)
    raise ValueError(f"unknown localizer method {method!r}")


def diagnose(recorder: TraceRecorder, run: StoredRun, localizer: Localizer) -> tuple[str, Diagnosis]:
    """Run a localizer on a stored run and save the diagnosis."""
    diagnosis = localizer.localize(view_of(run))
    diagnosis_id = recorder.insert(
        "diagnoses",
        {
            "run_id": run.id,
            "method": diagnosis.method,
            "predicted_step": diagnosis.predicted_step,
            "failure_type": diagnosis.failure_type.value if diagnosis.failure_type else None,
            "reason": diagnosis.reason,
            "evidence_json": json.dumps([e.model_dump() for e in diagnosis.evidence]),
            "checks_passed_json": json.dumps(diagnosis.checks_passed),
            "tokens": diagnosis.tokens,
            "seconds": diagnosis.seconds,
            "judge_model": diagnosis.judge_model,
            "judge_temperature": diagnosis.judge_temperature,
            "raw_response": diagnosis.raw_response,
            "used_llm": int(diagnosis.used_llm),
            "created_at": now(),
        },
    )
    return diagnosis_id, diagnosis


def load_diagnosis(row: dict[str, Any]) -> Diagnosis:
    return Diagnosis(
        method=row["method"],
        predicted_step=row["predicted_step"],
        failure_type=row["failure_type"],
        reason=row["reason"],
        evidence=json.loads(row["evidence_json"]),
        tokens=row["tokens"],
        seconds=row["seconds"],
        used_llm=bool(row["used_llm"]),
        checks_passed=json.loads(row["checks_passed_json"]),
        judge_model=row["judge_model"],
        judge_temperature=row["judge_temperature"],
        raw_response=row["raw_response"],
    )
