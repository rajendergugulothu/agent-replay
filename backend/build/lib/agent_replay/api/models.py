"""Request and response models for the HTTP API. The frontend's types mirror these."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RUN_ID_PATTERN = r"^[0-9a-f]{32}$"
SCENARIO_ID_PATTERN = r"^[a-z0-9_]{1,64}$"


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorOut(BaseModel):
    error: ErrorBody


class HealthOut(BaseModel):
    status: Literal["ok"]
    mode: Literal["live", "offline"]
    demo_mode: bool
    version: str
    results_available: bool


class ScenarioOut(BaseModel):
    id: str
    title: str
    description: str
    failure_type: str
    student_id: str
    user_request: str
    label_status: str


class ToolOut(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class StartRunIn(_Request):
    scenario_id: str = Field(pattern=SCENARIO_ID_PATTERN)


class RunStarted(BaseModel):
    run_id: str


class StepOut(BaseModel):
    step_number: int
    tool_name: str | None
    tool_args: dict[str, Any] | None
    tool_output: dict[str, Any] | None
    text: str | None
    summary: str
    tokens: int
    seconds: float
    depends_on: list[int]
    origin: Literal["model", "forced", "reused"]
    # What the fault injector changed at this step. Hidden until the run has
    # been diagnosed, so the diagnose screen does not give the answer away.
    injection: list[dict[str, Any]] | None


class ReasonOut(BaseModel):
    code: str
    message: str


class ValidationOut(BaseModel):
    passed: bool
    reasons: list[ReasonOut]


class ScheduleEntry(BaseModel):
    section_id: str
    course_code: str
    title: str
    days: list[str]
    start: str
    end: str
    credits: int


class RunOut(BaseModel):
    id: str
    scenario_id: str | None
    kind: Literal["original", "full_restart", "continuation"]
    status: Literal["running", "passed", "failed", "error"]
    stop_reason: str | None
    parent_run_id: str | None
    branch_step: int | None
    continuation_mode: str | None
    student_id: str
    user_request: str
    model: str
    steps: list[StepOut]
    validation: ValidationOut | None
    final_schedule: list[ScheduleEntry] | None
    injected_step: int | None
    observed_failure_step: int | None
    tokens: int
    seconds: float


class LocalizeIn(_Request):
    method: Literal["rule", "llm", "hybrid"]


class EvidenceOut(BaseModel):
    step_number: int
    field: str
    value: Any = None
    note: str = ""


class DiagnosisOut(BaseModel):
    diagnosis_id: str
    method: Literal["rule", "llm", "hybrid"]
    predicted_step: int | None
    failure_type: str | None
    reason: str
    evidence: list[EvidenceOut]
    tokens: int
    seconds: float
    used_llm: bool
    affected_steps: list[int]
    # Where the fault was actually planted, shown next to the prediction.
    injected_step: int | None


class ResumeIn(_Request):
    step: int = Field(ge=1, le=100)
    mode: Literal["forced_action", "repair_hint"]
    diagnosis_id: str | None = Field(default=None, pattern=RUN_ID_PATTERN)
    tool_name: str | None = Field(default=None, max_length=64)
    arguments: dict[str, Any] | None = None


class TokenSplit(BaseModel):
    localization: int
    repair: int
    replay: int
    total: int


class RunCost(BaseModel):
    run_id: str
    kind: str
    status: str
    passed: bool
    tokens: TokenSplit
    seconds: float
    steps_reused: int
    steps_new: int


class CompareOut(BaseModel):
    original: RunCost
    other: RunCost
    restart: RunCost | None


class ResetOut(BaseModel):
    runs_cleared: int
