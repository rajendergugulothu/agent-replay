"""Rule-based localizer. No LLM.

The rules are written against the tool schemas, the catalog and the user's
stated constraints. None of them knows about any particular scenario.
"""

import json
import time
from collections.abc import Callable

from agent_replay.catalog import load_catalog
from agent_replay.localizers.base import Diagnosis, Evidence, LocalizerView, Method, ViewStep
from agent_replay.models import FailureType
from agent_replay.planner.tools import TOOLS, argument_errors

Finding = tuple[FailureType, str, list[Evidence]]
Check = Callable[[ViewStep, LocalizerView], Finding | None]


def check_tool_exists(step: ViewStep, view: LocalizerView) -> Finding | None:
    if step.tool_name in TOOLS:
        return None
    return (
        FailureType.WRONG_TOOL,
        f"Step {step.step_number} called {step.tool_name!r}, which is not one of the agent's tools.",
        [Evidence(step_number=step.step_number, field="tool_name", value=step.tool_name)],
    )


def check_argument_schema(step: ViewStep, view: LocalizerView) -> Finding | None:
    assert step.tool_name is not None
    errors = argument_errors(step.tool_name, step.tool_args or {})
    if not errors:
        return None
    return (
        FailureType.INVALID_ARGUMENTS,
        f"Step {step.step_number} passed arguments that do not match the {step.tool_name} schema: {errors[0]}.",
        [
            Evidence(
                step_number=step.step_number, field="tool_args", value=step.tool_args, note="; ".join(errors)
            )
        ],
    )


def check_value_ranges(step: ViewStep, view: LocalizerView) -> Finding | None:
    catalog = load_catalog()
    args = step.tool_args or {}
    problems: list[Evidence] = []
    if "student_id" in args and args["student_id"] != view.student_id:
        problems.append(
            Evidence(
                step_number=step.step_number,
                field="tool_args.student_id",
                value=args["student_id"],
                note=f"not the student this run is for ({view.student_id})",
            )
        )
    if "course_code" in args and args["course_code"] not in catalog.courses:
        problems.append(
            Evidence(
                step_number=step.step_number,
                field="tool_args.course_code",
                value=args["course_code"],
                note="not in the catalog",
            )
        )
    for section_id in args.get("section_ids", []):
        if section_id not in catalog.sections:
            problems.append(
                Evidence(
                    step_number=step.step_number,
                    field="tool_args.section_ids",
                    value=section_id,
                    note="not in the catalog",
                )
            )
    if not problems:
        return None
    first = problems[0]
    return (
        FailureType.WRONG_ARGUMENT_VALUES,
        f"Step {step.step_number} used {first.value!r} for {first.field.split('.')[-1]}: {first.note}.",
        problems,
    )


def check_output(step: ViewStep, view: LocalizerView) -> Finding | None:
    """A well-formed call that still came back as an error is a tool failure.
    A rejected build_schedule is the tool doing its job, not failing."""
    output = step.tool_output or {}
    if "error" not in output or step.tool_name == "build_schedule":
        return None
    return (
        FailureType.TOOL_FAILURE,
        f"Step {step.step_number}: {step.tool_name} returned an error for a valid call: {output['error']}.",
        [Evidence(step_number=step.step_number, field="tool_output.error", value=output["error"])],
    )


def check_dependencies(step: ViewStep, view: LocalizerView) -> Finding | None:
    """An argument should match a value an earlier step produced: a course
    should come from the requirements that were fetched, a section from a
    search that was run."""
    args = step.tool_args or {}
    earlier = [s for s in view.steps if s.step_number < step.step_number and s.tool_output]

    if step.tool_name in {"check_prerequisite", "search_sections"} and "course_code" in args:
        known: set[str] = set()
        source: int | None = None
        for s in earlier:
            if s.tool_name == "get_degree_requirements" and "remaining_required" in (s.tool_output or {}):
                output = s.tool_output or {}
                known = {c["course_code"] for c in output["remaining_required"]} | set(
                    output.get("must_take_this_term", [])
                )
                source = s.step_number
        # Courses the user named are fair to look up even if they are electives.
        if source and args["course_code"] not in known and args["course_code"] not in view.user_request:
            return (
                FailureType.WRONG_ARGUMENT_VALUES,
                f"Step {step.step_number} looked up {args['course_code']}, which is not among the "
                f"courses returned by step {source} or named by the user.",
                [
                    Evidence(
                        step_number=step.step_number, field="tool_args.course_code", value=args["course_code"]
                    ),
                    Evidence(
                        step_number=source, field="tool_output.remaining_required", note="does not contain it"
                    ),
                ],
            )

    if step.tool_name == "build_schedule":
        searched = {
            section["section_id"]: s.step_number
            for s in earlier
            if s.tool_name == "search_sections"
            for section in (s.tool_output or {}).get("sections", [])
        }
        unknown = [sid for sid in args.get("section_ids", []) if sid not in searched]
        if unknown:
            return (
                FailureType.WRONG_ARGUMENT_VALUES,
                f"Step {step.step_number} scheduled {unknown[0]}, which no earlier section search returned.",
                [Evidence(step_number=step.step_number, field="tool_args.section_ids", value=unknown)],
            )
    return None


def check_constraints(step: ViewStep, view: LocalizerView) -> Finding | None:
    """The user's time rule, checked where breaking it is certain: a schedule
    built with a section that starts too early. A search without the time
    filter is not flagged, because the agent may still pick late sections."""
    earliest = view.constraints.earliest_start
    if not earliest:
        return None
    args = step.tool_args or {}
    if step.tool_name == "build_schedule":
        catalog = load_catalog()
        early = [
            sid
            for sid in args.get("section_ids", [])
            if sid in catalog.sections and catalog.sections[sid].start < earliest
        ]
        if early:
            return (
                FailureType.GOAL_NOT_MET,
                f"Step {step.step_number} scheduled {early[0]}, which starts before {earliest}.",
                [Evidence(step_number=step.step_number, field="tool_args.section_ids", value=early)],
            )
    return None


def check_repeated_call(step: ViewStep, view: LocalizerView) -> Finding | None:
    for earlier in view.steps:
        if earlier.step_number >= step.step_number:
            break
        if (
            earlier.tool_name == step.tool_name
            and earlier.tool_args == step.tool_args
            and earlier.tool_output == step.tool_output
            and "error" not in (earlier.tool_output or {})
        ):
            return (
                FailureType.WASTED_STEPS,
                f"Step {step.step_number} repeats step {earlier.step_number} exactly and learns nothing new.",
                [
                    Evidence(step_number=step.step_number, field="tool_args", value=step.tool_args),
                    Evidence(step_number=earlier.step_number, field="tool_args", value=earlier.tool_args),
                ],
            )
    return None


# Order matters: the first check that fails names the failure type for a step.
CHECKS: list[tuple[str, Check]] = [
    ("tool_exists", check_tool_exists),
    ("argument_schema", check_argument_schema),
    ("value_ranges", check_value_ranges),
    ("tool_output", check_output),
    ("dependencies", check_dependencies),
    ("user_constraints", check_constraints),
    ("repeated_call", check_repeated_call),
]


def _has_dependents(step_number: int, view: LocalizerView) -> bool:
    return any(step_number in later.depends_on for later in view.steps if later.step_number > step_number)


class RuleBasedLocalizer:
    method: Method = "rule"

    def localize(self, view: LocalizerView) -> Diagnosis:
        started = time.perf_counter()
        last = view.observed_failure_step or (view.steps[-1].step_number if view.steps else 0)
        findings: dict[int, Finding] = {}
        failed_checks: set[str] = set()
        # Walk backward from where the failure was seen.
        for step in reversed(view.steps):
            if step.step_number > last or step.tool_name is None:
                continue
            for name, check in CHECKS:
                if name != "tool_exists" and step.tool_name not in TOOLS:
                    break
                finding = check(step, view)
                if finding:
                    findings[step.step_number] = finding
                    failed_checks.add(name)
                    break

        seconds = time.perf_counter() - started
        passed = [name for name, _ in CHECKS if name not in failed_checks]
        if not findings:
            return Diagnosis(
                method="rule",
                predicted_step=None,
                failure_type=None,
                reason="No rule-based check failed on any step.",
                seconds=seconds,
                checks_passed=passed,
            )
        # Prefer the earliest failing step that a later step built on. A failing
        # step nothing depends on may be a slip the agent already corrected.
        ordered = sorted(findings)
        with_dependents = [n for n in ordered if _has_dependents(n, view)]
        chosen = (with_dependents or ordered)[0]
        failure_type, reason, evidence = findings[chosen]
        return Diagnosis(
            method="rule",
            predicted_step=chosen,
            failure_type=failure_type,
            reason=reason,
            evidence=evidence,
            seconds=seconds,
            checks_passed=passed,
        )


def compact_trace(view: LocalizerView) -> str:
    """The trace as compact JSON lines, one per step. Shared with the judge."""
    lines = []
    for step in view.steps:
        entry: dict[str, object] = {"step": step.step_number}
        if step.tool_name is None:
            entry["final_answer"] = step.text
        else:
            entry.update(tool=step.tool_name, arguments=step.tool_args, output=step.tool_output)
        lines.append(json.dumps(entry, separators=(",", ":")))
    return "\n".join(lines)
