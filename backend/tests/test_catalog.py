import re
from itertools import combinations, product

from agent_replay.catalog import load_catalog
from agent_replay.models import Constraints
from agent_replay.validator import validate_schedule


def test_catalog_loads_expected_shape() -> None:
    catalog = load_catalog()
    assert len(catalog.courses) == 25
    assert len(catalog.students) == 3
    assert all(2 <= len(course.sections) <= 3 for course in catalog.courses.values())
    assert all(re.fullmatch(r"QCS \d{3}", code) for code in catalog.courses)


def test_prerequisites_refer_to_real_courses_and_include_a_chain() -> None:
    catalog = load_catalog()
    for course in catalog.courses.values():
        assert set(course.prerequisites) <= set(catalog.courses)

    def depth(code: str) -> int:
        return 1 + max((depth(p) for p in catalog.courses[code].prerequisites), default=0)

    assert max(depth(code) for code in catalog.courses) >= 4


def test_students_only_reference_catalog_courses() -> None:
    catalog = load_catalog()
    for student in catalog.students.values():
        assert set(student.completed) | set(student.must_take) <= set(catalog.courses)
        for code in student.must_take:
            assert not catalog.missing_prerequisites(student, code)


def test_every_student_has_a_valid_schedule_after_10am() -> None:
    """If no valid schedule existed, a failed run would say nothing about the agent."""
    catalog = load_catalog()
    for student in catalog.students.values():
        eligible = [
            code
            for code in catalog.courses
            if code not in student.completed and not catalog.missing_prerequisites(student, code)
        ]
        found = any(
            validate_schedule(student, list(ids), Constraints(earliest_start="10:00")).passed
            for size in (3, 4)
            for combo in combinations(eligible, size)
            for ids in product(*[[s.id for s in catalog.courses[c].sections] for c in combo])
        )
        assert found, student.id
