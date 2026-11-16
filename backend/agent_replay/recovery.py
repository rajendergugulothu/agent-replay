"""Recovery: start runs, continue from a checkpoint, restart, and replay.

Agent continuation restores a checkpoint and lets the model act again. It is
what every verification and recovery uses. Deterministic replay makes no model
calls and only shows, and checks, what was recorded.
"""

import json
from typing import Any, Literal

from pydantic import BaseModel

from agent_replay.catalog import load_catalog
from agent_replay.injection import FaultInjector, Scenario
from agent_replay.models import (
    FAILURE_TYPE_DESCRIPTIONS,
    Checkpoint,
    Constraints,
    FailureType,
    StoredRun,
    StoredStep,
    ToolCall,
)
from agent_replay.planner.agent import AgentSettings, initial_state, run_agent
from agent_replay.tracing import TraceRecorder
from agent_replay.validator import validate_schedule

ContinuationMode = Literal["forced_action", "repair_hint", "regenerate"]


def start_scenario_run(
    recorder: TraceRecorder,
    scenario: Scenario,
    settings: AgentSettings,
    *,
    purpose: str,
    experiment_id: str | None = None,
    trial: int | None = None,
) -> tuple[str, AgentSettings, FaultInjector]:
    """Create the run row for an original (fault-injected) run. Split from the
    run itself so the API can return the id before the agent starts."""
    if scenario.limits.max_steps is not None:
        settings = AgentSettings(
            model=settings.model,
            temperature=settings.temperature,
            max_steps=min(settings.max_steps, scenario.limits.max_steps),
            max_tokens=settings.max_tokens,
        )
    run_id = recorder.start_run(
        scenario_id=scenario.id,
        kind="original",
        purpose=purpose,
        student_id=scenario.task.student_id,
        user_request=scenario.task.user_request,
        constraints=scenario.task.constraints,
        model=settings.model,
        temperature=settings.temperature,
        experiment_id=experiment_id,
        trial=trial,
    )
    return run_id, settings, FaultInjector(scenario.injections)


def run_scenario(
    client: Any,
    recorder: TraceRecorder,
    scenario: Scenario,
    settings: AgentSettings,
    *,
    purpose: str,
    experiment_id: str | None = None,
    trial: int | None = None,
) -> str:
    run_id, settings, injector = start_scenario_run(
        recorder, scenario, settings, purpose=purpose, experiment_id=experiment_id, trial=trial
    )
    run_agent(
        client=client,
        recorder=recorder,
        run_id=run_id,
        student_id=scenario.task.student_id,
        constraints=scenario.task.constraints,
        state=initial_state(scenario.task.student_id, scenario.task.user_request),
        settings=settings,
        injector=injector,
    )
    return run_id


def start_full_restart(
    recorder: TraceRecorder, parent: StoredRun, settings: AgentSettings, purpose: str
) -> str:
    return recorder.start_run(
        scenario_id=parent.scenario_id,
        kind="full_restart",
        purpose=purpose,
        student_id=parent.student_id,
        user_request=parent.user_request,
        constraints=parent.constraints,
        model=settings.model,
        temperature=settings.temperature,
        parent_run_id=parent.id,
        experiment_id=parent.experiment_id,
        trial=parent.trial,
    )


def run_full_restart(client: Any, recorder: TraceRecorder, run_id: str, settings: AgentSettings) -> None:
    """Same task from step 1 with no injector: the fault is a one-time event."""
    run = recorder.load_run(run_id)
    run_agent(
        client=client,
        recorder=recorder,
        run_id=run_id,
        student_id=run.student_id,
        constraints=run.constraints,
        state=initial_state(run.student_id, run.user_request),
        settings=settings,
    )


def full_restart(
    client: Any,
    recorder: TraceRecorder,
    parent_run_id: str,
    settings: AgentSettings,
    purpose: str = "experiment",
) -> str:
    run_id = start_full_restart(recorder, recorder.load_run(parent_run_id), settings, purpose)
    run_full_restart(client, recorder, run_id, settings)
    return run_id


def build_repair_hint(step: StoredStep, failure_type: FailureType | None) -> str:
    """Fixed template from the step, the call made there, and the failure type.
    The localizer's free-text reason is left out on purpose: two localizers that
    agree on step and type then produce the same hint and can share one run."""
    if step.tool_name:
        action = f"you called {step.tool_name} with arguments {json.dumps(step.tool_args, sort_keys=True)}"
    else:
        action = "you gave your final answer"
    diagnosis = f" The diagnosis: {FAILURE_TYPE_DESCRIPTIONS[failure_type]}." if failure_type else ""
    return (
        f"Debugger note: a review of this run flagged step {step.step_number} as the point where it went wrong. "
        f"At that step {action}.{diagnosis} "
        "You are back at that step. Do not repeat that action. Choose the correct action and continue the task."
    )


def original_action(step: StoredStep) -> ToolCall | None:
    """The call the model made before the injector changed it, if it was changed."""
    for log in step.injection or []:
        if log["kind"] in {"replace_argument", "replace_tool_call", "repeat_call"}:
            return ToolCall.model_validate(log["original"])
    if step.injection and step.tool_name and step.tool_args is not None:
        # replace_tool_output: the call itself was right, only its result was not.
        if any(log["kind"] == "replace_tool_output" for log in step.injection):
            return ToolCall(tool_name=step.tool_name, arguments=step.tool_args)
    return None


def start_continuation(
    recorder: TraceRecorder,
    parent: StoredRun,
    step: int,
    mode: ContinuationMode,
    settings: AgentSettings,
    *,
    hint: str | None = None,
    purpose: str = "experiment",
) -> str:
    """Create the continuation run and copy the reused steps into it."""
    parent.step(step)
    run_id = recorder.start_run(
        scenario_id=parent.scenario_id,
        kind="continuation",
        purpose=purpose,
        student_id=parent.student_id,
        user_request=parent.user_request,
        constraints=parent.constraints,
        model=settings.model,
        temperature=settings.temperature,
        parent_run_id=parent.id,
        branch_step=step,
        continuation_mode=mode,
        repair_hint=hint if mode == "repair_hint" else None,
        experiment_id=parent.experiment_id,
        trial=parent.trial,
    )
    recorder.copy_reused_steps(run_id, parent, up_to_step=step - 1)
    return run_id


def run_continuation(
    client: Any,
    recorder: TraceRecorder,
    run_id: str,
    settings: AgentSettings,
    forced_action: ToolCall | None = None,
) -> None:
    """Restore the checkpoint from just before the branch step and run forward.
    Earlier steps were copied from storage and are never re-called."""
    run = recorder.load_run(run_id)
    assert run.parent_run_id is not None and run.branch_step is not None
    parent = recorder.load_run(run.parent_run_id)
    state: Checkpoint = parent.step(run.branch_step).checkpoint.model_copy(deep=True)
    if run.continuation_mode == "repair_hint" and run.repair_hint:
        state.messages.append({"role": "user", "content": run.repair_hint})
    if run.continuation_mode == "forced_action" and forced_action is None:
        raise ValueError("forced_action mode needs an action")

    earlier = [s for s in parent.steps if s.step_number < run.branch_step]
    prior_outputs = [(s.step_number, json.dumps(s.tool_output)) for s in earlier if s.tool_output is not None]
    schedule_steps = [
        s.step_number
        for s in earlier
        if s.tool_name == "build_schedule" and (s.tool_output or {}).get("ok") is True
    ]
    run_agent(
        client=client,
        recorder=recorder,
        run_id=run_id,
        student_id=run.student_id,
        constraints=run.constraints,
        state=state,
        settings=settings,
        forced_action=forced_action if run.continuation_mode == "forced_action" else None,
        prior_outputs=prior_outputs,
        schedule_step=schedule_steps[-1] if schedule_steps else None,
    )


def continue_run(
    client: Any,
    recorder: TraceRecorder,
    *,
    parent_run_id: str,
    step: int,
    mode: ContinuationMode,
    settings: AgentSettings,
    forced_action: ToolCall | None = None,
    hint: str | None = None,
    purpose: str = "experiment",
) -> str:
    parent = recorder.load_run(parent_run_id)
    run_id = start_continuation(recorder, parent, step, mode, settings, hint=hint, purpose=purpose)
    run_continuation(client, recorder, run_id, settings, forced_action)
    return run_id


def resume_at_true_step(
    client: Any,
    recorder: TraceRecorder,
    original: StoredRun,
    settings: AgentSettings,
    purpose: str = "experiment",
) -> str:
    """Fix only the injected step and continue. The fix is the action the
    model chose before the injector changed it. A dropped constraint has no
    such action, so that step is regenerated from the unaltered checkpoint."""
    if original.injected_step is None:
        raise ValueError(f"run {original.id} has no injected step")
    action = original_action(original.step(original.injected_step))
    return continue_run(
        client,
        recorder,
        parent_run_id=original.id,
        step=original.injected_step,
        mode="forced_action" if action else "regenerate",
        settings=settings,
        forced_action=action,
        purpose=purpose,
    )


class ReplayResult(BaseModel):
    run_id: str
    steps_replayed: int
    matches: bool
    mismatches: list[str]


def replay_deterministic(recorder: TraceRecorder, run_id: str) -> ReplayResult:
    """Rebuild a stored run from recorded outputs with zero model calls and
    check that it reproduces what was stored. A mismatch means the recorder
    missed something a continuation would need."""
    run = recorder.load_run(run_id)
    mismatches: list[str] = []
    if not run.steps:
        return ReplayResult(run_id=run_id, steps_replayed=0, matches=True, mismatches=[])

    messages = list(run.steps[0].checkpoint.messages)
    schedule: list[str] | None = run.steps[0].checkpoint.latest_schedule
    for step in run.steps:
        # The first step's checkpoint is the starting point and already holds the
        # hint when the run branched at step 1.
        if step.step_number == run.branch_step and run.repair_hint and step is not run.steps[0]:
            messages.append({"role": "user", "content": run.repair_hint})
        if step.origin == "reused":
            # Reused steps were verified in the run they came from, and that run
            # may have had its own repair hint, so their chain is taken as stored.
            messages = list(step.checkpoint.messages)
        elif step.checkpoint.messages != messages:
            mismatches.append(f"step {step.step_number}: checkpoint does not follow from earlier steps")
            messages = list(step.checkpoint.messages)
        if step.checkpoint.latest_schedule != schedule:
            mismatches.append(f"step {step.step_number}: schedule in checkpoint differs")
        messages.append(step.model_output)
        if step.tool_name is not None:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": step.tool_call_id,
                    "content": json.dumps(step.tool_output),
                }
            )
            if step.tool_name == "build_schedule" and (step.tool_output or {}).get("ok") is True:
                schedule = list((step.tool_output or {})["schedule"]["section_ids"])

    if schedule != run.final_schedule:
        mismatches.append("final schedule differs from the stored one")
    if run.stop_reason == "finished":
        student = load_catalog().students[run.student_id]
        validation = validate_schedule(student, schedule, Constraints.model_validate(run.constraints))
        if validation != run.validation:
            mismatches.append("validator verdict differs from the stored one")
    return ReplayResult(
        run_id=run_id, steps_replayed=len(run.steps), matches=not mismatches, mismatches=mismatches
    )
