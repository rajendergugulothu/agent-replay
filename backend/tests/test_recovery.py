import pytest
from fake_model import FakeClient

from agent_replay.injection import Scenario
from agent_replay.models import FailureType, StoredRun, ToolCall
from agent_replay.planner.agent import AgentSettings
from agent_replay.recovery import (
    build_repair_hint,
    continue_run,
    full_restart,
    original_action,
    replay_deterministic,
    resume_at_true_step,
    run_scenario,
)
from agent_replay.tracing import TraceRecorder


@pytest.fixture
def failed(recorder: TraceRecorder, agent_settings: AgentSettings, scenario_01: Scenario) -> StoredRun:
    return recorder.load_run(
        run_scenario(FakeClient(), recorder, scenario_01, agent_settings, purpose="test")
    )


def test_continuation_reuses_earlier_steps_without_model_calls(
    recorder: TraceRecorder, agent_settings: AgentSettings, failed: StoredRun
) -> None:
    client = FakeClient()
    hint = build_repair_hint(failed.step(3), FailureType.WRONG_ARGUMENT_VALUES)
    run = recorder.load_run(
        continue_run(
            client,
            recorder,
            parent_run_id=failed.id,
            step=3,
            mode="repair_hint",
            settings=agent_settings,
            hint=hint,
        )
    )
    assert (run.kind, run.parent_run_id, run.branch_step) == ("continuation", failed.id, 3)
    assert [s.origin for s in run.steps[:2]] == ["reused", "reused"]
    assert all(s.origin == "model" for s in run.steps[2:])
    # One model call per new step, none for the two reused ones.
    assert len(client.calls) == len(run.steps) - 2
    for number in (1, 2):
        assert run.step(number).tool_output == failed.step(number).tool_output
        assert run.step(number).reused_from_step_id == failed.step(number).id
    # Totals count only what this run actually spent.
    assert run.total_tokens == sum(s.tokens for s in run.steps if s.origin != "reused")
    assert run.total_tokens < sum(s.tokens for s in run.steps)
    # The first model call saw the restored history plus the hint.
    assert client.calls[0]["messages"][:-1] == failed.step(3).checkpoint.messages
    assert client.calls[0]["messages"][-1] == {"role": "user", "content": hint}
    assert run.status == "passed"


def test_forced_action_changes_only_step_n(
    recorder: TraceRecorder, agent_settings: AgentSettings, failed: StoredRun
) -> None:
    client = FakeClient()
    action = ToolCall(
        tool_name="check_prerequisite", arguments={"student_id": "stu_01", "course_code": "QCS 340"}
    )
    run = recorder.load_run(
        continue_run(
            client,
            recorder,
            parent_run_id=failed.id,
            step=3,
            mode="forced_action",
            settings=agent_settings,
            forced_action=action,
        )
    )
    forced = run.step(3)
    assert forced.origin == "forced" and forced.tokens == 0
    assert forced.tool_args == action.arguments
    assert forced.tool_output == {"course_code": "QCS 340", "met": False, "missing": ["QCS 240"]}
    assert forced.checkpoint.messages == failed.step(3).checkpoint.messages
    assert [s.origin for s in run.steps] == ["reused", "reused", "forced"] + ["model"] * (len(run.steps) - 3)
    assert len(client.calls) == len(run.steps) - 3
    assert run.injected_step is None and run.status == "passed"


def test_forced_action_mode_requires_an_action(
    recorder: TraceRecorder, agent_settings: AgentSettings, failed: StoredRun
) -> None:
    with pytest.raises(ValueError):
        continue_run(
            FakeClient(),
            recorder,
            parent_run_id=failed.id,
            step=3,
            mode="forced_action",
            settings=agent_settings,
        )
    with pytest.raises(KeyError):
        continue_run(
            FakeClient(),
            recorder,
            parent_run_id=failed.id,
            step=99,
            mode="regenerate",
            settings=agent_settings,
        )


def test_resume_at_true_step_uses_the_call_the_model_originally_made(
    recorder: TraceRecorder, agent_settings: AgentSettings, failed: StoredRun
) -> None:
    assert original_action(failed.step(3)) == ToolCall(
        tool_name="check_prerequisite", arguments={"student_id": "stu_01", "course_code": "QCS 340"}
    )
    assert original_action(failed.step(2)) is None
    run = recorder.load_run(resume_at_true_step(FakeClient(), recorder, failed, agent_settings))
    assert run.branch_step == 3 and run.continuation_mode == "forced_action"
    assert run.step(3).tool_args == {"student_id": "stu_01", "course_code": "QCS 340"}


def test_full_restart_runs_from_step_one_with_no_fault(
    recorder: TraceRecorder, agent_settings: AgentSettings, failed: StoredRun
) -> None:
    run = recorder.load_run(full_restart(FakeClient(), recorder, failed.id, agent_settings))
    assert run.kind == "full_restart" and run.parent_run_id == failed.id
    assert all(s.origin == "model" and s.injection is None for s in run.steps)
    assert run.status == "passed"


def test_repair_hint_is_a_fixed_template(failed: StoredRun) -> None:
    hint = build_repair_hint(failed.step(3), FailureType.WRONG_ARGUMENT_VALUES)
    assert "step 3" in hint and "check_prerequisite" in hint and "QCS 310" in hint
    assert hint == build_repair_hint(failed.step(3), FailureType.WRONG_ARGUMENT_VALUES)
    assert hint != build_repair_hint(failed.step(3), FailureType.TOOL_FAILURE)
    assert "final answer" in build_repair_hint(failed.steps[-1], None)


def test_deterministic_replay_reproduces_stored_runs_with_no_model_calls(
    recorder: TraceRecorder, agent_settings: AgentSettings, failed: StoredRun
) -> None:
    hint = build_repair_hint(failed.step(3), FailureType.WRONG_ARGUMENT_VALUES)
    continued = continue_run(
        FakeClient(),
        recorder,
        parent_run_id=failed.id,
        step=3,
        mode="repair_hint",
        settings=agent_settings,
        hint=hint,
    )
    # A continuation of a continuation: the reused steps carry an earlier hint.
    nested = continue_run(
        FakeClient(), recorder, parent_run_id=continued, step=5, mode="regenerate", settings=agent_settings
    )
    for run_id in (
        failed.id,
        continued,
        nested,
        resume_at_true_step(FakeClient(), recorder, failed, agent_settings),
    ):
        result = replay_deterministic(recorder, run_id)
        assert result.matches, result.mismatches
        assert result.steps_replayed == len(recorder.load_run(run_id).steps)


def test_replay_notices_a_tampered_trace(recorder: TraceRecorder, failed: StoredRun) -> None:
    recorder.execute(
        "UPDATE steps SET tool_output_json = '{\"met\": false}' WHERE run_id = ? AND step_number = 3",
        (failed.id,),
    )
    result = replay_deterministic(recorder, failed.id)
    assert not result.matches and "step 4" in result.mismatches[0]
