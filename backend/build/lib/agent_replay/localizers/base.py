"""Shared localizer types. All three localizers take the same view of a failed
run and return the same Diagnosis, so the experiment can swap them."""

from typing import Any, Literal, Protocol

from pydantic import BaseModel

from agent_replay.models import Constraints, FailureType, StoredRun, ValidationResult

Method = Literal["rule", "llm", "hybrid"]


class ViewStep(BaseModel):
    step_number: int
    tool_name: str | None
    tool_args: dict[str, Any] | None
    tool_output: dict[str, Any] | None
    text: str | None
    depends_on: list[int]


class LocalizerView(BaseModel):
    """What a developer debugging a real agent would see. It deliberately has
    no injection log, no scenario, and no hint about which steps were scripted."""

    run_id: str
    student_id: str
    user_request: str
    constraints: Constraints
    steps: list[ViewStep]
    status: str
    stop_reason: str | None
    validation: ValidationResult | None
    observed_failure_step: int | None


def view_of(run: StoredRun) -> LocalizerView:
    return LocalizerView(
        run_id=run.id,
        student_id=run.student_id,
        user_request=run.user_request,
        constraints=run.constraints,
        steps=[
            ViewStep(
                step_number=s.step_number,
                tool_name=s.tool_name,
                tool_args=s.tool_args,
                tool_output=s.tool_output,
                text=s.model_output.get("content"),
                depends_on=s.depends_on,
            )
            for s in run.steps
        ],
        status=run.status,
        stop_reason=run.stop_reason,
        validation=run.validation,
        observed_failure_step=run.observed_failure_step,
    )


class Evidence(BaseModel):
    step_number: int
    field: str
    value: Any = None
    note: str = ""


class Diagnosis(BaseModel):
    method: Method
    predicted_step: int | None
    failure_type: FailureType | None
    reason: str
    evidence: list[Evidence] = []
    tokens: int = 0
    seconds: float = 0.0
    used_llm: bool = False
    checks_passed: list[str] = []
    judge_model: str | None = None
    judge_temperature: float | None = None
    raw_response: str | None = None


class Localizer(Protocol):
    method: Method

    def localize(self, view: LocalizerView) -> Diagnosis: ...
