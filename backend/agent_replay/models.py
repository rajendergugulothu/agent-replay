"""Shared data types. Everything that crosses a module boundary is defined here."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class FailureType(StrEnum):
    """Agent failure types from Chip Huyen's AI Engineering."""

    WRONG_TOOL = "wrong_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    WRONG_ARGUMENT_VALUES = "wrong_argument_values"
    GOAL_NOT_MET = "goal_not_met"
    TOOL_FAILURE = "tool_failure"
    WASTED_STEPS = "wasted_steps"


FAILURE_TYPE_DESCRIPTIONS: dict[FailureType, str] = {
    FailureType.WRONG_TOOL: "the agent called the wrong tool for this step",
    FailureType.INVALID_ARGUMENTS: "the arguments did not match the tool's schema",
    FailureType.WRONG_ARGUMENT_VALUES: "the arguments were well formed but had the wrong values",
    FailureType.GOAL_NOT_MET: "the action ignored part of what the user asked for",
    FailureType.TOOL_FAILURE: "the tool returned a wrong or failed result",
    FailureType.WASTED_STEPS: "the step repeated work and wasted the step budget",
}


class ToolCall(BaseModel):
    tool_name: str
    arguments: dict[str, Any]


class Constraints(BaseModel):
    earliest_start: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class Section(BaseModel):
    id: str
    course_code: str
    days: list[str]
    start: str
    end: str


class Course(BaseModel):
    code: str
    title: str
    credits: int
    prerequisites: list[str]
    sections: list[Section]


class Student(BaseModel):
    id: str
    name: str
    major: str
    completed: list[str]
    must_take: list[str]
    min_credits: int
    max_credits: int


ReasonCode = Literal[
    "unmet_prerequisite",
    "time_conflict",
    "before_earliest_time",
    "missing_required_course",
    "credit_limit_exceeded",
    "credit_minimum_not_met",
    "invalid_schedule",
    "no_schedule",
    "budget_exhausted",
]


class ValidationReason(BaseModel):
    code: ReasonCode
    message: str
    course_codes: list[str] = []
    section_ids: list[str] = []


class ValidationResult(BaseModel):
    passed: bool
    reasons: list[ValidationReason] = []


class Checkpoint(BaseModel):
    """Full agent state just before a step's model call."""

    step_number: int
    messages: list[dict[str, Any]]
    latest_schedule: list[str] | None
    tokens_used: int
    steps_used: int


class StepRecord(BaseModel):
    step_number: int
    model_input: list[dict[str, Any]]
    model_output: dict[str, Any]
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_output: dict[str, Any] | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    checkpoint: Checkpoint
    depends_on: list[int] = []
    injection: list[dict[str, Any]] | None = None
    origin: Literal["model", "forced", "reused"] = "model"

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class StoredStep(StepRecord):
    id: str
    run_id: str
    reused_from_step_id: str | None = None


class StoredRun(BaseModel):
    id: str
    scenario_id: str | None
    kind: Literal["original", "full_restart", "continuation"]
    purpose: str
    experiment_id: str | None
    trial: int | None
    parent_run_id: str | None
    branch_step: int | None
    continuation_mode: str | None
    repair_hint: str | None
    agent_name: str
    student_id: str
    user_request: str
    constraints: Constraints
    model: str
    temperature: float | None
    status: Literal["running", "passed", "failed", "error"]
    stop_reason: str | None
    final_schedule: list[str] | None
    validation: ValidationResult | None
    injected_step: int | None
    observed_failure_step: int | None
    unfired_injections: int
    prompt_tokens: int
    completion_tokens: int
    total_seconds: float
    created_at: str
    ended_at: str | None
    steps: list[StoredStep] = []

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def step(self, number: int) -> StoredStep:
        for step in self.steps:
            if step.step_number == number:
                return step
        raise KeyError(f"run {self.id} has no step {number}")
