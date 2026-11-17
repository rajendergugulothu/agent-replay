"""The synthetic course catalog and student profiles, loaded from catalog.json."""

import json
from functools import lru_cache
from pathlib import Path

from agent_replay.models import Course, Section, Student

CATALOG_PATH = Path(__file__).with_name("catalog.json")


class Catalog:
    def __init__(self, courses: list[Course], students: list[Student], required: list[str]) -> None:
        self.courses: dict[str, Course] = {course.code: course for course in courses}
        self.students: dict[str, Student] = {student.id: student for student in students}
        self.required: list[str] = required
        self.sections: dict[str, Section] = {
            section.id: section for course in courses for section in course.sections
        }

    def remaining_required(self, student: Student) -> list[Course]:
        done = set(student.completed)
        return [self.courses[code] for code in self.required if code not in done]

    def missing_prerequisites(self, student: Student, course_code: str) -> list[str]:
        done = set(student.completed)
        return [code for code in self.courses[course_code].prerequisites if code not in done]


def read_catalog(path: Path) -> Catalog:
    data = json.loads(path.read_text())
    return Catalog(
        courses=[Course.model_validate(item) for item in data["courses"]],
        students=[Student.model_validate(item) for item in data["students"]],
        required=list(data["degree"]["required"]),
    )


@lru_cache(maxsize=1)
def load_catalog() -> Catalog:
    return read_catalog(CATALOG_PATH)
