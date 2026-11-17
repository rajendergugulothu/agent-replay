"""The semester planner: one tool-calling loop used for fresh runs and for
continuations. A fresh run starts from an empty state, a continuation from a
stored checkpoint. There is no second loop for recovery."""

import copy
import json
import time
from dataclasses import dataclass
from typing import Any

from agent_replay.catalog import load_catalog
from agent_replay.config import Settings
from agent_replay.injection import FaultInjector
from agent_replay.llm import OfflineMiss
from agent_replay.models import (
    Checkpoint,
    Constraints,
    StepRecord,
    ToolCall,
    ValidationReason,
    ValidationResult,
)
from agent_replay.planner.tools import execute_tool, tool_schemas
from agent_replay.tracing import TraceRecorder
from agent_replay.validator import validate_schedule

SYSTEM_PROMPT = """You are a semester planning agent for a university's Computer Science major.
Build a valid schedule for the student using the tools. Call one tool at a time.

How to work:
1. Get the student's degree requirements.
2. Pick the courses to take. Include every course the student must take this term and stay within the credit limits.
3. Check the prerequisite for every course before you schedule it. Drop any course whose prerequisite is not met and pick another.
4. Search for sections of each course that fit the student's time constraints.
5. Call build_schedule with the chosen section ids.

The last successful build_schedule call is the schedule that gets submitted. When it is right, reply with a short summary and no tool call."""


class AgentError(RuntimeError):
    """The model call failed. The run is stored with status 'error'."""


@dataclass(frozen=True)
class AgentSettings:
    model: str
    temperature: float | None
    max_steps: int
    max_tokens: int
    # Caps one reply. The run budget is only checked between steps, so without
    # this a single call could overshoot it by any amount.
    max_completion_tokens: int = 4_000


def agent_settings_from(settings: Settings) -> AgentSettings:
    return AgentSettings(
        model=settings.openai_model or "unset",
        temperature=settings.agent_temperature,
        max_steps=settings.max_steps_per_run,
        max_tokens=settings.max_tokens_per_run,
        max_completion_tokens=settings.max_completion_tokens,
    )


def initial_state(student_id: str, user_request: str) -> Checkpoint:
    return Checkpoint(
        step_number=1,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Student id: {student_id}. {user_request}"},
        ],
        latest_schedule=None,
        tokens_used=0,
        steps_used=0,
    )


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    return []


def compute_depends_on(
    arguments: dict[str, Any], prior_outputs: list[tuple[int, str]], given_text: str
) -> list[int]:
    """Step N depends on step M when a value in N's arguments first appeared in
    M's output. Values the user supplied (such as the student id) do not count.
    This sees data flow only, not every way an earlier step shaped a decision."""
    depends: set[int] = set()
    for value in _strings(arguments):
        if len(value) < 3 or value in given_text:
            continue
        for step_number, output_text in prior_outputs:
            if json.dumps(value) in output_text:
                depends.add(step_number)
                break
    return sorted(depends)


def _assistant_message(content: str | None, call_id: str, name: str, arguments: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
        ],
    }


def run_agent(
    *,
    client: Any,
    recorder: TraceRecorder,
    run_id: str,
    student_id: str,
    constraints: Constraints,
    state: Checkpoint,
    settings: AgentSettings,
    injector: FaultInjector | None = None,
    forced_action: ToolCall | None = None,
    prior_outputs: list[tuple[int, str]] | None = None,
    schedule_step: int | None = None,
) -> None:
    """Run the loop from `state` until the model stops calling tools or a
    budget runs out, recording every step. Ends the run in the store."""
    state = state.model_copy(deep=True)
    outputs = list(prior_outputs or [])
    given_text = " ".join(str(m.get("content") or "") for m in state.messages[:2])
    student = load_catalog().students[student_id]

    def finish(
        status: str, stop_reason: str, validation: ValidationResult | None, failed_at: int | None
    ) -> None:
        recorder.end_run(
            run_id,
            status=status,
            stop_reason=stop_reason,
            final_schedule=state.latest_schedule,
            validation=validation,
            observed_failure_step=failed_at,
            unfired_injections=len(injector.unfired()) if injector else 0,
        )

    while True:
        step_number = state.steps_used + 1
        for limit_hit, name in (
            (state.steps_used >= settings.max_steps, "max_steps"),
            (state.tokens_used >= settings.max_tokens, "max_tokens"),
        ):
            if limit_hit:
                reason = ValidationReason(
                    code="budget_exhausted",
                    message=f"The run hit its {name.replace('_', ' ')} limit before the agent finished.",
                )
                finish("failed", name, ValidationResult(passed=False, reasons=[reason]), state.steps_used)
                return

        checkpoint = state.model_copy(deep=True, update={"step_number": step_number})
        logs: list[dict[str, Any]] = []
        started = time.perf_counter()
        prompt_tokens = completion_tokens = 0
        origin = "model"
        content: str | None = None
        raw_arguments: str | None = None
        call: ToolCall | None = None
        # Snapshot now: state.messages grows once this step's result is appended.
        model_input = copy.deepcopy(state.messages)

        if forced_action is not None:
            # A forced step has no model call: the given action stands in for
            # whatever the model would have chosen here.
            call, forced_action = forced_action, None
            call_id = f"forced_{step_number}"
            origin = "forced"
        else:
            if injector:
                model_input, input_logs = injector.alter_model_input(step_number, model_input)
                logs += input_logs
            request: dict[str, Any] = {
                "model": settings.model,
                "messages": model_input,
                "tools": tool_schemas(),
                "parallel_tool_calls": False,
                "max_completion_tokens": settings.max_completion_tokens,
            }
            if settings.temperature is not None:
                request["temperature"] = settings.temperature
            try:
                response = client.chat.completions.create(**request)
            except OfflineMiss:
                finish("error", "offline_unavailable", None, None)
                raise
            except Exception as exc:
                finish("error", "api_error", None, None)
                raise AgentError(f"model call failed at step {step_number}: {exc}") from exc
            message = response.choices[0].message
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens
            content = message.content
            if message.tool_calls:
                # parallel_tool_calls is off, but if more than one call comes
                # back anyway only the first is kept so a step stays one action.
                first = message.tool_calls[0]
                call_id = first.id
                try:
                    parsed = json.loads(first.function.arguments or "{}")
                    if not isinstance(parsed, dict):
                        raise ValueError("arguments must be a JSON object")
                    call = ToolCall(tool_name=first.function.name, arguments=parsed)
                except ValueError:
                    raw_arguments = first.function.arguments
                    call = ToolCall(tool_name=first.function.name, arguments={})

        state.tokens_used += prompt_tokens + completion_tokens
        state.steps_used += 1

        if call is None:
            assistant = {"role": "assistant", "content": content}
            state.messages.append(assistant)
            validation = validate_schedule(student, state.latest_schedule, constraints)
            recorder.record_step(
                run_id,
                StepRecord(
                    step_number=step_number,
                    model_input=model_input,
                    model_output=assistant,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    seconds=time.perf_counter() - started,
                    checkpoint=checkpoint,
                    depends_on=[schedule_step] if schedule_step else [],
                    injection=logs or None,
                ),
            )
            finish(
                "passed" if validation.passed else "failed",
                "finished",
                validation,
                None if validation.passed else step_number,
            )
            return

        if raw_arguments is not None:
            output: dict[str, Any] = {"error": "arguments were not valid JSON"}
            arguments_text = raw_arguments
        else:
            if injector:
                call, call_logs = injector.alter_tool_call(step_number, call)
                logs += call_logs
            output = execute_tool(call.tool_name, call.arguments)
            if injector:
                output, output_logs = injector.alter_tool_output(step_number, output)
                logs += output_logs
            arguments_text = json.dumps(call.arguments)

        # History holds the call as executed. An injected fault therefore reads
        # as the agent's own action, both to the model and to a localizer.
        assistant = _assistant_message(content, call_id, call.tool_name, arguments_text)
        output_text = json.dumps(output)
        state.messages.append(assistant)
        state.messages.append({"role": "tool", "tool_call_id": call_id, "content": output_text})

        depends_on = compute_depends_on(call.arguments, outputs, given_text)
        outputs.append((step_number, output_text))
        if call.tool_name == "build_schedule" and output.get("ok") is True:
            state.latest_schedule = list(output["schedule"]["section_ids"])
            schedule_step = step_number

        recorder.record_step(
            run_id,
            StepRecord(
                step_number=step_number,
                model_input=model_input,
                model_output=assistant,
                tool_name=call.tool_name,
                tool_call_id=call_id,
                tool_args=call.arguments if raw_arguments is None else {"_unparsed": raw_arguments},
                tool_output=output,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                seconds=time.perf_counter() - started,
                checkpoint=checkpoint,
                depends_on=depends_on,
                injection=logs or None,
                origin=origin,  # type: ignore[arg-type]
            ),
        )
