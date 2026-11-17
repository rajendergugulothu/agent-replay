"""The four planner tools, their argument schemas, and one function to run them."""

from collections.abc import Callable
from itertools import combinations
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_replay.catalog import load_catalog
from agent_replay.validator import sections_overlap

TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


class _Args(BaseModel):
    # Strict types and no extra fields, so a malformed call is caught here and
    # reported back to the model instead of being silently coerced.
    model_config = ConfigDict(extra="forbid", strict=True)


# Ids are short. The length caps keep an edited argument from carrying a
# paragraph of text into the model's context.
ShortText = Annotated[str, Field(min_length=1, max_length=24)]


class GetDegreeRequirementsArgs(_Args):
    student_id: ShortText


class CheckPrerequisiteArgs(_Args):
    student_id: ShortText
    course_code: ShortText


class SearchSectionsArgs(_Args):
    course_code: ShortText
    earliest_start: str | None = Field(
        default=None,
        pattern=TIME_PATTERN,
        description='24-hour "HH:MM". Only sections starting at or after it.',
    )


class BuildScheduleArgs(_Args):
    student_id: ShortText
    section_ids: list[ShortText] = Field(min_length=1, max_length=10)


def get_degree_requirements(args: GetDegreeRequirementsArgs) -> dict[str, Any]:
    catalog = load_catalog()
    student = catalog.students.get(args.student_id)
    if student is None:
        return {"error": f"unknown student_id {args.student_id!r}"}
    return {
        "student_id": student.id,
        "major": student.major,
        "completed": student.completed,
        "remaining_required": [
            {"course_code": course.code, "title": course.title, "credits": course.credits}
            for course in catalog.remaining_required(student)
        ],
        "must_take_this_term": student.must_take,
        "min_credits": student.min_credits,
        "max_credits": student.max_credits,
    }


def check_prerequisite(args: CheckPrerequisiteArgs) -> dict[str, Any]:
    catalog = load_catalog()
    student = catalog.students.get(args.student_id)
    if student is None:
        return {"error": f"unknown student_id {args.student_id!r}"}
    if args.course_code not in catalog.courses:
        return {"error": f"unknown course_code {args.course_code!r}"}
    missing = catalog.missing_prerequisites(student, args.course_code)
    return {"course_code": args.course_code, "met": not missing, "missing": missing}


def search_sections(args: SearchSectionsArgs) -> dict[str, Any]:
    catalog = load_catalog()
    course = catalog.courses.get(args.course_code)
    if course is None:
        return {"error": f"unknown course_code {args.course_code!r}"}
    sections = [
        {"section_id": s.id, "days": s.days, "start": s.start, "end": s.end}
        for s in course.sections
        if args.earliest_start is None or s.start >= args.earliest_start
    ]
    return {
        "course_code": course.code,
        "title": course.title,
        "credits": course.credits,
        "sections": sections,
    }


def build_schedule(args: BuildScheduleArgs) -> dict[str, Any]:
    """Checks only that sections exist and do not overlap. Prerequisites, the
    earliest-start rule and required courses are left to the validator, which
    is what lets an early mistake reach the end of a run."""
    catalog = load_catalog()
    if args.student_id not in catalog.students:
        return {"ok": False, "error": f"unknown student_id {args.student_id!r}"}
    unknown = [sid for sid in args.section_ids if sid not in catalog.sections]
    if unknown:
        return {"ok": False, "error": "unknown section ids", "unknown_section_ids": unknown}
    sections = [catalog.sections[sid] for sid in args.section_ids]
    conflicts = [[a.id, b.id] for a, b in combinations(sections, 2) if sections_overlap(a, b)]
    if conflicts:
        return {"ok": False, "error": "time conflict", "conflicts": conflicts}
    courses = [
        {
            "course_code": s.course_code,
            "section_id": s.id,
            "days": s.days,
            "start": s.start,
            "end": s.end,
            "credits": catalog.courses[s.course_code].credits,
        }
        for s in sections
    ]
    return {
        "ok": True,
        "schedule": {
            "section_ids": list(args.section_ids),
            "courses": courses,
            "total_credits": sum(c["credits"] for c in courses),
        },
    }


class Tool(BaseModel):
    name: str
    description: str
    args_model: type[_Args]
    function: Callable[[Any], dict[str, Any]]


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in [
        Tool(
            name="get_degree_requirements",
            description=(
                "Get a student's completed courses, remaining required courses, "
                "courses they must take this term, and credit limits."
            ),
            args_model=GetDegreeRequirementsArgs,
            function=get_degree_requirements,
        ),
        Tool(
            name="check_prerequisite",
            description="Check whether a student has met the prerequisites for one course.",
            args_model=CheckPrerequisiteArgs,
            function=check_prerequisite,
        ),
        Tool(
            name="search_sections",
            description="List the sections of one course with days and times.",
            args_model=SearchSectionsArgs,
            function=search_sections,
        ),
        Tool(
            name="build_schedule",
            description=(
                "Build a schedule from section ids. Fails on unknown sections or time "
                "conflicts. The last successful call is the schedule that gets submitted."
            ),
            args_model=BuildScheduleArgs,
            function=build_schedule,
        ),
    ]
}


def parameters_schema(tool: Tool) -> dict[str, Any]:
    schema = tool.args_model.model_json_schema()
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
    return schema


def tool_schemas() -> list[dict[str, Any]]:
    """Tool definitions in the format the Chat Completions API expects."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": parameters_schema(tool),
            },
        }
        for tool in TOOLS.values()
    ]


def argument_errors(tool_name: str, arguments: dict[str, Any]) -> list[str]:
    """Schema problems with a call, as plain strings. Empty means valid."""
    tool = TOOLS.get(tool_name)
    if tool is None:
        return [f"unknown tool {tool_name!r}"]
    try:
        tool.args_model.model_validate(arguments)
    except ValidationError as exc:
        return [
            f"{'.'.join(str(p) for p in err['loc']) or 'arguments'}: {err['msg']}" for err in exc.errors()
        ]
    return []


def execute_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run a tool. Bad calls come back as an error result, never an exception,
    because the model has to see the error to react to it."""
    tool = TOOLS.get(tool_name)
    if tool is None:
        return {"error": f"unknown tool {tool_name!r}", "available_tools": list(TOOLS)}
    errors = argument_errors(tool_name, arguments)
    if errors:
        return {"error": "invalid arguments", "details": errors}
    return tool.function(tool.args_model.model_validate(arguments))
