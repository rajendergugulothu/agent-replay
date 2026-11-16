"""Scenario files and the fault injector.

The injector is the only scripted part of a run. It changes one thing at one
step, writes down what it changed, and leaves every other step alone.
"""

import hashlib
import re
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, model_validator

from agent_replay.models import Constraints, FailureType, ToolCall

SCENARIO_ID_PATTERN = re.compile(r"^[a-z0-9_]{1,64}$")


class FaultKind(str, Enum):
    REPLACE_ARGUMENT = "replace_argument"
    REPLACE_TOOL_CALL = "replace_tool_call"
    REPLACE_TOOL_OUTPUT = "replace_tool_output"
    REPEAT_CALL = "repeat_call"
    DROP_CONSTRAINT = "drop_constraint"


class Target(BaseModel):
    tool: str | None = None
    occurrence: int = 1
    where: dict[str, Any] = {}
    at_step: int | None = None


class Injection(BaseModel):
    kind: FaultKind
    failure_type: FailureType
    target: Target
    argument: str | None = None
    value: Any = None
    times: int = 1

    @model_validator(mode="after")
    def _check_shape(self) -> "Injection":
        if self.kind is FaultKind.DROP_CONSTRAINT:
            if self.target.at_step is None or not isinstance(self.value, str):
                raise ValueError("drop_constraint needs target.at_step and a text value to remove")
        elif self.target.tool is None:
            raise ValueError(f"{self.kind.value} needs target.tool")
        if self.kind is FaultKind.REPLACE_ARGUMENT and not self.argument:
            raise ValueError("replace_argument needs an argument name")
        if self.kind is FaultKind.REPLACE_TOOL_CALL:
            ToolCall.model_validate(self.value)
        if self.kind is FaultKind.REPLACE_TOOL_OUTPUT and not isinstance(self.value, dict):
            raise ValueError("replace_tool_output needs a mapping value")
        return self


class Task(BaseModel):
    student_id: str
    user_request: str
    constraints: Constraints = Constraints()


class Expected(BaseModel):
    injected_step: int | None = None
    observed_failure_step: int | None = None
    validator_reasons: list[str] = []


class Limits(BaseModel):
    max_steps: int | None = None


class Scenario(BaseModel):
    id: str
    title: str
    description: str = ""
    task: Task
    injections: list[Injection]
    expected: Expected = Expected()
    limits: Limits = Limits()

    @property
    def failure_type(self) -> FailureType:
        return self.injections[0].failure_type


def scenario_path(scenarios_dir: Path, scenario_id: str) -> Path:
    """Resolve an id to a file inside scenarios_dir. The id pattern keeps a
    request from naming a path outside the folder."""
    if not SCENARIO_ID_PATTERN.match(scenario_id):
        raise KeyError(f"invalid scenario id {scenario_id!r}")
    path = scenarios_dir / f"{scenario_id}.yaml"
    if not path.is_file():
        raise KeyError(f"no scenario with id {scenario_id!r}")
    return path


def load_scenario(path: Path) -> Scenario:
    scenario = Scenario.model_validate(yaml.safe_load(path.read_text()))
    if scenario.id != path.stem:
        raise ValueError(f"{path.name}: id {scenario.id!r} does not match the file name")
    return scenario


def load_scenarios(scenarios_dir: Path) -> list[Scenario]:
    return [load_scenario(path) for path in sorted(scenarios_dir.glob("*.yaml"))]


def scenario_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FaultInjector:
    def __init__(self, injections: list[Injection]) -> None:
        self.injections = injections
        self._seen = [0] * len(injections)
        self._fired = [False] * len(injections)
        self._repeats_left = [0] * len(injections)
        self._output_due: dict[int, int] = {}
        self._last_call: ToolCall | None = None

    def alter_model_input(
        self, step_number: int, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return the messages to send at this step. Only drop_constraint acts here."""
        logs: list[dict[str, Any]] = []
        for index, injection in enumerate(self.injections):
            if injection.kind is not FaultKind.DROP_CONSTRAINT:
                continue
            start = injection.target.at_step or 0
            if not start <= step_number < start + injection.times:
                continue
            text = str(injection.value)
            altered = [
                {**m, "content": m["content"].replace(text, "")}
                if m.get("role") == "user" and isinstance(m.get("content"), str)
                else m
                for m in messages
            ]
            if altered != messages:
                messages = altered
                self._fired[index] = True
                logs.append(self._log(injection, original=text, injected=None))
        return messages, logs

    def alter_tool_call(self, step_number: int, call: ToolCall) -> tuple[ToolCall, list[dict[str, Any]]]:
        """Called as the call goes out. Returns the call to execute and to
        write into the agent's history."""
        intended = call
        logs: list[dict[str, Any]] = []
        for index, injection in enumerate(self.injections):
            if injection.kind is FaultKind.DROP_CONSTRAINT:
                continue
            if self._repeats_left[index] > 0 and self._last_call is not None:
                self._repeats_left[index] -= 1
                logs.append(self._log(injection, intended.model_dump(), self._last_call.model_dump()))
                call = self._last_call
                continue
            if self._fired[index] or not self._matches(injection.target, intended):
                continue
            self._seen[index] += 1
            if self._seen[index] != injection.target.occurrence:
                continue
            if injection.kind is FaultKind.REPLACE_TOOL_OUTPUT:
                self._output_due[step_number] = index
                self._fired[index] = True
                continue
            if injection.kind is FaultKind.REPEAT_CALL:
                if self._last_call is None:
                    continue
                altered = self._last_call
                self._repeats_left[index] = injection.times - 1
            elif injection.kind is FaultKind.REPLACE_TOOL_CALL:
                altered = ToolCall.model_validate(injection.value)
            else:
                assert injection.argument is not None
                altered = ToolCall(
                    tool_name=call.tool_name,
                    arguments={**call.arguments, injection.argument: injection.value},
                )
            self._fired[index] = True
            logs.append(self._log(injection, intended.model_dump(), altered.model_dump()))
            call = altered
        self._last_call = call
        return call, logs

    def alter_tool_output(
        self, step_number: int, output: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        index = self._output_due.pop(step_number, None)
        if index is None:
            return output, []
        injection = self.injections[index]
        return dict(injection.value), [self._log(injection, output, injection.value)]

    def unfired(self) -> list[Injection]:
        """Injections whose target never came up in the run."""
        return [inj for inj, fired in zip(self.injections, self._fired, strict=True) if not fired]

    @staticmethod
    def _matches(target: Target, call: ToolCall) -> bool:
        return call.tool_name == target.tool and all(
            call.arguments.get(key) == value for key, value in target.where.items()
        )

    @staticmethod
    def _log(injection: Injection, original: Any, injected: Any) -> dict[str, Any]:
        return {
            "kind": injection.kind.value,
            "failure_type": injection.failure_type.value,
            "argument": injection.argument,
            "original": original,
            "injected": injected,
        }
