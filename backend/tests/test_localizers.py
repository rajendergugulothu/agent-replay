import json
from typing import Any

import pytest
from fake_model import FakeClient

from agent_replay.localizers import (
    HybridLocalizer,
    LLMJudgeLocalizer,
    RuleBasedLocalizer,
    diagnose,
    load_diagnosis,
    make_localizer,
    view_of,
)
from agent_replay.localizers.base import LocalizerView, ViewStep
from agent_replay.localizers.llm_judge import build_prompt
from agent_replay.models import Constraints, FailureType, ValidationReason, ValidationResult
from agent_replay.planner.tools import execute_tool

REQUIREMENTS = execute_tool("get_degree_requirements", {"student_id": "stu_01"})


def step(
    number: int,
    tool: str | None,
    args: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    depends_on: list[int] | None = None,
) -> ViewStep:
    if tool and output is None:
        output = execute_tool(tool, args or {})
    return ViewStep(
        step_number=number,
        tool_name=tool,
        tool_args=args,
        tool_output=output,
        text=None if tool else "done",
        depends_on=depends_on or [],
    )


def view(steps: list[ViewStep], earliest: str | None = "10:00") -> LocalizerView:
    return LocalizerView(
        run_id="r",
        student_id="stu_01",
        user_request="Build my schedule.",
        constraints=Constraints(earliest_start=earliest),
        steps=steps,
        status="failed",
        stop_reason="finished",
        observed_failure_step=steps[-1].step_number,
        validation=ValidationResult(
            passed=False, reasons=[ValidationReason(code="unmet_prerequisite", message="m")]
        ),
    )


FIRST = step(1, "get_degree_requirements", {"student_id": "stu_01"})
FINAL = step(9, None)


def rule(steps: list[ViewStep], **kwargs: Any):  # type: ignore[no-untyped-def]
    return RuleBasedLocalizer().localize(view(steps, **kwargs))


def test_clean_trace_has_no_finding() -> None:
    result = rule(
        [
            FIRST,
            step(2, "check_prerequisite", {"student_id": "stu_01", "course_code": "QCS 240"}, depends_on=[1]),
            step(3, "search_sections", {"course_code": "QCS 240", "earliest_start": "10:00"}, depends_on=[1]),
            step(4, "build_schedule", {"student_id": "stu_01", "section_ids": ["QCS240-02"]}, depends_on=[3]),
            FINAL,
        ]
    )
    assert result.predicted_step is None and result.failure_type is None and result.tokens == 0
    assert "argument_schema" in result.checks_passed


def test_unknown_tool() -> None:
    result = rule([FIRST, step(2, "register_student", {"student_id": "stu_01"}), FINAL])
    assert (result.predicted_step, result.failure_type) == (2, FailureType.WRONG_TOOL)


def test_schema_violation() -> None:
    result = rule(
        [FIRST, step(2, "search_sections", {"course_code": "QCS 240", "earliest_start": 1000}), FINAL]
    )
    assert (result.predicted_step, result.failure_type) == (2, FailureType.INVALID_ARGUMENTS)


def test_value_not_in_catalog_and_wrong_student() -> None:
    result = rule(
        [FIRST, step(2, "check_prerequisite", {"student_id": "stu_01", "course_code": "QCS 999"}), FINAL]
    )
    assert (result.predicted_step, result.failure_type) == (2, FailureType.WRONG_ARGUMENT_VALUES)
    result = rule([step(1, "get_degree_requirements", {"student_id": "stu_02"}), FINAL])
    assert result.predicted_step == 1 and result.evidence[0].field == "tool_args.student_id"


def test_tool_error_on_a_valid_call() -> None:
    broken = step(2, "search_sections", {"course_code": "QCS 240"}, output={"error": "service unavailable"})
    result = rule([FIRST, broken, FINAL])
    assert (result.predicted_step, result.failure_type) == (2, FailureType.TOOL_FAILURE)


def test_dependency_rules() -> None:
    # QCS 110 is an elective: not in the requirements step 1 returned.
    result = rule(
        [FIRST, step(2, "search_sections", {"course_code": "QCS 110", "earliest_start": "10:00"}), FINAL]
    )
    assert (result.predicted_step, result.failure_type) == (2, FailureType.WRONG_ARGUMENT_VALUES)
    # A section no search returned.
    result = rule(
        [
            FIRST,
            step(2, "search_sections", {"course_code": "QCS 240", "earliest_start": "10:00"}, depends_on=[1]),
            step(
                3,
                "build_schedule",
                {"student_id": "stu_01", "section_ids": ["QCS240-02", "QCS301-02"]},
                depends_on=[2],
            ),
            FINAL,
        ]
    )
    assert result.predicted_step == 3 and "no earlier section search" in result.reason


def test_schedule_that_breaks_the_time_rule() -> None:
    steps = [
        FIRST,
        step(2, "search_sections", {"course_code": "QCS 240"}, depends_on=[1]),
        step(3, "build_schedule", {"student_id": "stu_01", "section_ids": ["QCS240-01"]}, depends_on=[2]),
        FINAL,
    ]
    assert rule(steps).failure_type is FailureType.GOAL_NOT_MET
    assert rule(steps, earliest=None).predicted_step is None


def test_repeated_call() -> None:
    args = {"student_id": "stu_01", "course_code": "QCS 240"}
    result = rule([FIRST, step(2, "check_prerequisite", args), step(3, "check_prerequisite", args), FINAL])
    assert (result.predicted_step, result.failure_type) == (3, FailureType.WASTED_STEPS)


def test_prefers_the_earliest_failing_step_that_later_steps_depend_on() -> None:
    result = rule(
        [
            FIRST,
            # Fails a check, but nothing builds on it.
            step(2, "check_prerequisite", {"student_id": "stu_01", "course_code": "QCS 999"}),
            # Fails a check, and step 4 depends on it.
            step(3, "search_sections", {"course_code": "QCS 110"}, depends_on=[1]),
            step(4, "build_schedule", {"student_id": "stu_01", "section_ids": ["QCS110-02"]}, depends_on=[3]),
            FINAL,
        ]
    )
    assert result.predicted_step == 3


def judge_with(reply: Any) -> LLMJudgeLocalizer:
    return LLMJudgeLocalizer(FakeClient(judge=lambda kwargs: reply), "judge-model", 0.0)


CLEAN = view(
    [FIRST, step(2, "check_prerequisite", {"student_id": "stu_01", "course_code": "QCS 240"}), FINAL]
)


def test_judge_parses_a_structured_reply() -> None:
    judge = judge_with(
        {
            "predicted_step": 2,
            "failure_type": "wrong_argument_values",
            "reason": "Wrong course.",
            "evidence": [
                {"step_number": 2, "field": "arguments.course_code", "note": "mismatch"},
                {"step_number": 77, "field": "x", "note": "not a step"},
            ],
        }
    )
    result = judge.localize(CLEAN)
    assert (result.predicted_step, result.failure_type) == (2, FailureType.WRONG_ARGUMENT_VALUES)
    assert [e.step_number for e in result.evidence] == [2]
    assert result.tokens > 0 and result.used_llm and result.judge_temperature == 0.0
    request = judge.client.calls[0]
    assert request["model"] == "judge-model" and request["temperature"] == 0.0
    assert request["response_format"]["type"] == "json_schema"


@pytest.mark.parametrize(
    "reply",
    [
        "this is not json",
        '{"predicted_step": "three"}',
        {"predicted_step": 2, "failure_type": "made_up_type", "reason": "x", "evidence": []},
        {"predicted_step": 42, "failure_type": "wrong_tool", "reason": "x", "evidence": []},
    ],
)
def test_judge_handles_bad_replies_without_crashing(reply: Any) -> None:
    result = judge_with(reply).localize(CLEAN)
    assert result.predicted_step is None and result.failure_type is None
    # The tokens were spent either way and must be counted.
    assert result.tokens > 0 and result.raw_response


def test_judge_prompt_contains_the_trace_but_no_injection_log(
    recorder,
    client,
    agent_settings,
    scenario_01,  # type: ignore[no-untyped-def]
) -> None:
    from agent_replay.recovery import run_scenario

    run = recorder.load_run(run_scenario(client, recorder, scenario_01, agent_settings, purpose="test"))
    prompt = build_prompt(view_of(run), checks_passed=["argument_schema"])
    assert '"step":3' in prompt and "QCS 310" in prompt and "unmet_prerequisite" in prompt
    assert "argument_schema" in prompt
    # Nothing about what was planted or what the agent originally intended.
    assert "inject" not in prompt.lower() and "scenario" not in prompt.lower()
    assert "injection" not in json.dumps(view_of(run).model_dump())


def test_hybrid_spends_no_tokens_when_rules_find_the_step() -> None:
    fake = FakeClient()
    hybrid = HybridLocalizer(LLMJudgeLocalizer(fake, "judge-model"))
    result = hybrid.localize(view([FIRST, step(2, "register_student", {}), FINAL]))
    assert result.method == "hybrid" and result.predicted_step == 2
    assert result.tokens == 0 and not result.used_llm and fake.calls == []


def test_hybrid_falls_back_to_the_judge_and_tells_it_what_passed() -> None:
    fake = FakeClient(
        judge=lambda kwargs: {
            "predicted_step": 2,
            "failure_type": "wrong_argument_values",
            "reason": "r",
            "evidence": [],
        }
    )
    result = HybridLocalizer(LLMJudgeLocalizer(fake, "judge-model")).localize(CLEAN)
    assert result.method == "hybrid" and result.predicted_step == 2 and result.used_llm and result.tokens > 0
    assert "found nothing" in fake.calls[0]["messages"][0]["content"]


def test_diagnose_stores_and_reloads(recorder, client, agent_settings, scenario_01) -> None:  # type: ignore[no-untyped-def]
    from agent_replay.recovery import run_scenario

    run = recorder.load_run(run_scenario(client, recorder, scenario_01, agent_settings, purpose="test"))
    diagnosis_id, diagnosis = diagnose(recorder, run, make_localizer("llm", client, "judge-model", 0.0))
    row = recorder.query("SELECT * FROM diagnoses WHERE id = ?", (diagnosis_id,))[0]
    assert load_diagnosis(row) == diagnosis
    with pytest.raises(ValueError):
        make_localizer("magic", client, "m", 0.0)
