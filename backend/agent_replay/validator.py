"""Deterministic schedule validator. No LLM. Collects every reason it finds."""

from itertools import combinations

from agent_replay.catalog import Catalog, load_catalog
from agent_replay.models import Constraints, Section, Student, ValidationReason, ValidationResult


def sections_overlap(a: Section, b: Section) -> bool:
    # Times are zero-padded "HH:MM", so string comparison orders them correctly.
    return bool(set(a.days) & set(b.days)) and a.start < b.end and b.start < a.end


def validate_schedule(
    student: Student,
    section_ids: list[str] | None,
    constraints: Constraints,
    catalog: Catalog | None = None,
) -> ValidationResult:
    catalog = catalog or load_catalog()
    if not section_ids:
        return ValidationResult(
            passed=False,
            reasons=[ValidationReason(code="no_schedule", message="No schedule was submitted.")],
        )

    reasons: list[ValidationReason] = []
    sections: list[Section] = []
    for section_id in section_ids:
        section = catalog.sections.get(section_id)
        if section is None:
            reasons.append(
                ValidationReason(
                    code="invalid_schedule",
                    message=f"Section {section_id} does not exist.",
                    section_ids=[section_id],
                )
            )
        else:
            sections.append(section)

    codes = [section.course_code for section in sections]
    for code in sorted({code for code in codes if codes.count(code) > 1}):
        reasons.append(
            ValidationReason(
                code="invalid_schedule",
                message=f"{code} appears more than once in the schedule.",
                course_codes=[code],
            )
        )
    for code in sorted(set(codes) & set(student.completed)):
        reasons.append(
            ValidationReason(
                code="invalid_schedule",
                message=f"{code} was already completed by the student.",
                course_codes=[code],
            )
        )

    for code in sorted(set(codes)):
        missing = catalog.missing_prerequisites(student, code)
        if missing:
            reasons.append(
                ValidationReason(
                    code="unmet_prerequisite",
                    message=f"{code} needs {', '.join(missing)}, which the student has not completed.",
                    course_codes=[code, *missing],
                )
            )

    for a, b in combinations(sections, 2):
        if sections_overlap(a, b):
            reasons.append(
                ValidationReason(
                    code="time_conflict",
                    message=f"{a.id} and {b.id} meet at the same time.",
                    course_codes=[a.course_code, b.course_code],
                    section_ids=[a.id, b.id],
                )
            )

    if constraints.earliest_start:
        for section in sections:
            if section.start < constraints.earliest_start:
                reasons.append(
                    ValidationReason(
                        code="before_earliest_time",
                        message=(
                            f"{section.id} starts at {section.start}, "
                            f"before the earliest allowed time {constraints.earliest_start}."
                        ),
                        course_codes=[section.course_code],
                        section_ids=[section.id],
                    )
                )

    for code in student.must_take:
        if code not in codes:
            reasons.append(
                ValidationReason(
                    code="missing_required_course",
                    message=f"{code} is required this term and is not in the schedule.",
                    course_codes=[code],
                )
            )

    credits = sum(catalog.courses[code].credits for code in set(codes))
    if credits > student.max_credits:
        reasons.append(
            ValidationReason(
                code="credit_limit_exceeded",
                message=f"The schedule has {credits} credits, over the limit of {student.max_credits}.",
            )
        )
    if credits < student.min_credits:
        reasons.append(
            ValidationReason(
                code="credit_minimum_not_met",
                message=f"The schedule has {credits} credits, under the minimum of {student.min_credits}.",
            )
        )

    return ValidationResult(passed=not reasons, reasons=reasons)
