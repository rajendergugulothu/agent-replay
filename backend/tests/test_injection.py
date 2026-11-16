from pathlib import Path

import pytest
from conftest import SCENARIOS_DIR

from agent_replay.injection import (
    FaultInjector,
    FaultKind,
    Injection,
    Target,
    load_scenario,
    load_scenarios,
    scenario_path,
)
from agent_replay.models import FailureType, ToolCall

WRONG = FailureType.WRONG_ARGUMENT_VALUES


def check(code: str) -> ToolCall:
    return ToolCall(tool_name="check_prerequisite", arguments={"student_id": "stu_01", "course_code": code})


def replace_argument(**target: object) -> Injection:
    return Injection(
        kind=FaultKind.REPLACE_ARGUMENT,
        failure_type=WRONG,
        target=Target(tool="check_prerequisite", **target),  # type: ignore[arg-type]
        argument="course_code",
        value="QCS 310",
    )


def test_changes_exactly_one_argument_and_nothing_else() -> None:
    injector = FaultInjector([replace_argument(where={"course_code": "QCS 340"})])
    first, logs1 = injector.alter_tool_call(1, check("QCS 240"))
    second, logs2 = injector.alter_tool_call(2, check("QCS 340"))
    third, logs3 = injector.alter_tool_call(3, check("QCS 340"))
    assert first == check("QCS 240") and logs1 == []
    assert second.arguments == {"student_id": "stu_01", "course_code": "QCS 310"}
    assert logs2[0]["original"]["arguments"]["course_code"] == "QCS 340"
    assert logs2[0]["injected"]["arguments"]["course_code"] == "QCS 310"
    # It fires once. The same call later goes through untouched.
    assert third == check("QCS 340") and logs3 == []
    assert injector.unfired() == []


def test_occurrence_counts_matching_calls() -> None:
    injector = FaultInjector([replace_argument(occurrence=2)])
    assert injector.alter_tool_call(1, check("QCS 240"))[1] == []
    assert injector.alter_tool_call(2, ToolCall(tool_name="search_sections", arguments={}))[1] == []
    altered, logs = injector.alter_tool_call(3, check("QCS 230"))
    assert altered.arguments["course_code"] == "QCS 310" and len(logs) == 1


def test_unfired_when_the_target_never_occurs() -> None:
    injector = FaultInjector([replace_argument(where={"course_code": "QCS 999"})])
    injector.alter_tool_call(1, check("QCS 240"))
    assert len(injector.unfired()) == 1


def test_replace_tool_call() -> None:
    injection = Injection(
        kind=FaultKind.REPLACE_TOOL_CALL,
        failure_type=FailureType.WRONG_TOOL,
        target=Target(tool="check_prerequisite"),
        value={"tool_name": "search_sections", "arguments": {"course_code": "QCS 340"}},
    )
    altered, logs = FaultInjector([injection]).alter_tool_call(1, check("QCS 340"))
    assert altered.tool_name == "search_sections" and logs[0]["original"]["tool_name"] == "check_prerequisite"


def test_replace_tool_output_leaves_the_call_alone() -> None:
    injection = Injection(
        kind=FaultKind.REPLACE_TOOL_OUTPUT,
        failure_type=FailureType.TOOL_FAILURE,
        target=Target(tool="check_prerequisite"),
        value={"met": True},
    )
    injector = FaultInjector([injection])
    call, logs = injector.alter_tool_call(4, check("QCS 340"))
    assert call == check("QCS 340") and logs == []
    assert injector.alter_tool_output(3, {"met": False}) == ({"met": False}, [])
    output, logs = injector.alter_tool_output(4, {"met": False})
    assert output == {"met": True} and logs[0]["original"] == {"met": False}


def test_repeat_call_replays_the_previous_call() -> None:
    injection = Injection(
        kind=FaultKind.REPEAT_CALL,
        failure_type=FailureType.WASTED_STEPS,
        target=Target(tool="search_sections"),
        times=2,
    )
    injector = FaultInjector([injection])
    search = ToolCall(tool_name="search_sections", arguments={"course_code": "QCS 240"})
    injector.alter_tool_call(1, check("QCS 240"))
    assert injector.alter_tool_call(2, search)[0] == check("QCS 240")
    assert injector.alter_tool_call(3, search)[0] == check("QCS 240")
    assert injector.alter_tool_call(4, search)[0] == search


def test_drop_constraint_edits_only_the_listed_steps() -> None:
    injection = Injection(
        kind=FaultKind.DROP_CONSTRAINT,
        failure_type=FailureType.GOAL_NOT_MET,
        target=Target(at_step=2),
        value=" No classes before 10am.",
    )
    injector = FaultInjector([injection])
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "Plan it. No classes before 10am."},
    ]
    assert injector.alter_model_input(1, messages) == (messages, [])
    altered, logs = injector.alter_model_input(2, messages)
    assert altered[1]["content"] == "Plan it." and len(logs) == 1
    assert messages[1]["content"].endswith("10am.")
    assert injector.alter_model_input(3, messages)[1] == []


def test_injection_shape_is_validated() -> None:
    with pytest.raises(ValueError):
        Injection(kind=FaultKind.REPLACE_ARGUMENT, failure_type=WRONG, target=Target(tool="x"))
    with pytest.raises(ValueError):
        Injection(kind=FaultKind.DROP_CONSTRAINT, failure_type=WRONG, target=Target(), value="x")


def test_all_scenarios_load_and_cover_every_failure_type() -> None:
    scenarios = load_scenarios(SCENARIOS_DIR)
    assert len(scenarios) == 10
    assert {s.failure_type for s in scenarios} == set(FailureType)
    assert sum(len(s.injections) == 2 for s in scenarios) == 2


def test_scenario_path_rejects_traversal(tmp_path: Path) -> None:
    assert scenario_path(SCENARIOS_DIR, "scenario_01").name == "scenario_01.yaml"
    for bad in ("../secrets", "scenario_01.yaml", "a/b", "", "missing"):
        with pytest.raises(KeyError):
            scenario_path(SCENARIOS_DIR, bad)
    other = tmp_path / "other.yaml"
    other.write_text((SCENARIOS_DIR / "scenario_01.yaml").read_text())
    with pytest.raises(ValueError):
        load_scenario(other)
