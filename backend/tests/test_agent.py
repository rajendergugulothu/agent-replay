import json
from typing import Any

import pytest
from fake_model import FakeClient

from agent_replay.injection import Scenario
from agent_replay.llm import response_from_dict
from agent_replay.models import Constraints
from agent_replay.planner.agent import AgentError, AgentSettings, compute_depends_on, initial_state, run_agent
from agent_replay.recovery import run_scenario
from agent_replay.tracing import TraceRecorder


def test_clean_run_passes_and_is_fully_recorded(
    recorder: TraceRecorder, client: FakeClient, agent_settings: AgentSettings, scenario_01: Scenario
) -> None:
    clean = scenario_01.model_copy(update={"injections": []})
    run = recorder.load_run(run_scenario(client, recorder, clean, agent_settings, purpose="test"))
    assert run.status == "passed" and run.stop_reason == "finished"
    assert run.injected_step is None and run.observed_failure_step is None
    assert [s.step_number for s in run.steps] == list(range(1, len(run.steps) + 1))
    assert run.steps[-1].tool_name is None
    # Totals are the sum of what each step reported.
    assert run.total_tokens == sum(s.tokens for s in run.steps) > 0
    assert len(client.calls) == len(run.steps)
    assert all(call["parallel_tool_calls"] is False for call in client.calls)


def test_injected_fault_is_stored_and_reaches_the_validator(
    recorder: TraceRecorder, client: FakeClient, agent_settings: AgentSettings, scenario_01: Scenario
) -> None:
    run = recorder.load_run(run_scenario(client, recorder, scenario_01, agent_settings, purpose="test"))
    step = run.step(3)
    assert run.injected_step == 3
    assert step.tool_args == {"student_id": "stu_01", "course_code": "QCS 310"}
    assert step.injection and step.injection[0]["original"]["arguments"]["course_code"] == "QCS 340"
    # The agent's own history shows the injected call, not the one it intended.
    stored_call = step.model_output["tool_calls"][0]["function"]["arguments"]
    assert json.loads(stored_call)["course_code"] == "QCS 310"
    assert [s.step_number for s in run.steps if s.injection] == [3]
    assert run.status == "failed" and run.observed_failure_step == run.steps[-1].step_number
    assert run.validation and [r.code for r in run.validation.reasons] == ["unmet_prerequisite"]


def test_checkpoint_is_the_state_before_each_step(
    recorder: TraceRecorder, client: FakeClient, agent_settings: AgentSettings, scenario_01: Scenario
) -> None:
    run = recorder.load_run(run_scenario(client, recorder, scenario_01, agent_settings, purpose="test"))
    assert len(run.step(1).checkpoint.messages) == 2
    for step in run.steps[1:]:
        before = run.step(step.step_number - 1)
        assert len(step.checkpoint.messages) == len(before.checkpoint.messages) + 2
        assert step.checkpoint.tokens_used == before.checkpoint.tokens_used + before.tokens
        assert step.checkpoint.steps_used == step.step_number - 1


def test_step_budget_ends_the_run(recorder: TraceRecorder, client: FakeClient, scenario_01: Scenario) -> None:
    settings = AgentSettings(model="fake-model", temperature=None, max_steps=4, max_tokens=40_000)
    run = recorder.load_run(run_scenario(client, recorder, scenario_01, settings, purpose="test"))
    assert run.status == "failed" and run.stop_reason == "max_steps" and len(run.steps) == 4
    assert run.validation and run.validation.reasons[0].code == "budget_exhausted"


def test_token_budget_ends_the_run(
    recorder: TraceRecorder, client: FakeClient, scenario_01: Scenario
) -> None:
    settings = AgentSettings(model="fake-model", temperature=None, max_steps=15, max_tokens=1_000)
    run = recorder.load_run(run_scenario(client, recorder, scenario_01, settings, purpose="test"))
    assert run.stop_reason == "max_tokens" and len(run.steps) < 8


class _ScriptedClient(FakeClient):
    def __init__(self, replies: list[Any]) -> None:
        super().__init__()
        self.replies = replies

    def create(self, **kwargs: Any) -> Any:
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return response_from_dict(reply)


def _call(name: str, arguments: str, call_id: str = "c1") -> dict[str, Any]:
    return {
        "content": None,
        "tool_calls": [{"id": call_id, "name": name, "arguments": arguments}],
        "prompt_tokens": 10,
        "completion_tokens": 5,
    }


FINAL = {"content": "done", "tool_calls": [], "prompt_tokens": 10, "completion_tokens": 5}


def _run(recorder: TraceRecorder, settings: AgentSettings, replies: list[Any]) -> str:
    run_id = recorder.start_run(
        scenario_id=None,
        kind="original",
        purpose="test",
        student_id="stu_01",
        user_request="r",
        constraints=Constraints(),
        model="m",
        temperature=None,
    )
    run_agent(
        client=_ScriptedClient(replies),
        recorder=recorder,
        run_id=run_id,
        student_id="stu_01",
        constraints=Constraints(),
        state=initial_state("stu_01", "r"),
        settings=settings,
    )
    return run_id


def test_unparseable_arguments_and_unknown_tools_become_tool_errors(
    recorder: TraceRecorder, agent_settings: AgentSettings
) -> None:
    run = recorder.load_run(
        _run(
            recorder,
            agent_settings,
            [_call("search_sections", "{not json"), _call("delete_all", "{}", "c2"), FINAL],
        )
    )
    assert run.step(1).tool_output == {"error": "arguments were not valid JSON"}
    assert run.step(1).tool_args == {"_unparsed": "{not json"}
    assert "unknown tool" in run.step(2).tool_output["error"]  # type: ignore[index]
    assert run.status == "failed" and run.validation and run.validation.reasons[0].code == "no_schedule"


def test_only_the_first_of_several_tool_calls_is_kept(
    recorder: TraceRecorder, agent_settings: AgentSettings
) -> None:
    double = _call("check_prerequisite", '{"student_id": "stu_01", "course_code": "QCS 240"}')
    double["tool_calls"].append(
        {"id": "c9", "name": "search_sections", "arguments": '{"course_code": "QCS 240"}'}
    )
    run = recorder.load_run(_run(recorder, agent_settings, [double, FINAL]))
    assert len(run.step(1).model_output["tool_calls"]) == 1 and run.step(1).tool_name == "check_prerequisite"


def test_api_error_marks_the_run_as_error(recorder: TraceRecorder, agent_settings: AgentSettings) -> None:
    with pytest.raises(AgentError):
        _run(recorder, agent_settings, [RuntimeError("boom")])
    run = recorder.find_runs(kind="original")[0]
    assert run.status == "error" and run.stop_reason == "api_error"


def test_depends_on_follows_values_to_the_step_that_produced_them() -> None:
    outputs = [(1, json.dumps({"remaining": ["QCS 240"]})), (2, json.dumps({"sections": ["QCS240-02"]}))]
    assert compute_depends_on({"course_code": "QCS 240"}, outputs, "") == [1]
    assert compute_depends_on({"section_ids": ["QCS240-02"]}, outputs, "") == [2]
    # A value the user gave does not come from a step.
    assert compute_depends_on({"course_code": "QCS 240"}, outputs, "I want QCS 240") == []
