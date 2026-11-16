from agent_replay.catalog import load_catalog
from agent_replay.models import Constraints
from agent_replay.validator import validate_schedule

STUDENT = load_catalog().students["stu_01"]
VALID = ["QCS110-02", "QCS230-02", "QCS240-02", "QCS301-02"]
LATE = Constraints(earliest_start="10:00")


def codes(section_ids: list[str] | None, constraints: Constraints = LATE) -> list[str]:
    return [r.code for r in validate_schedule(STUDENT, section_ids, constraints).reasons]


def test_valid_schedule_passes() -> None:
    result = validate_schedule(STUDENT, VALID, LATE)
    assert result.passed and result.reasons == []


def test_no_schedule() -> None:
    assert codes(None) == ["no_schedule"]
    assert codes([]) == ["no_schedule"]


def test_unmet_prerequisite() -> None:
    # QCS 340 needs QCS 240, which stu_01 has not completed.
    assert codes(["QCS240-02", "QCS340-02", "QCS230-02", "QCS301-02"]) == ["unmet_prerequisite"]


def test_time_conflict() -> None:
    # QCS240-02 and QCS110-03 both meet Tue/Thu 12:30.
    assert codes(["QCS240-02", "QCS110-03", "QCS230-02", "QCS301-02"]) == ["time_conflict"]


def test_before_earliest_time() -> None:
    early = ["QCS110-01", "QCS230-02", "QCS240-02", "QCS301-02"]
    assert codes(early) == ["before_earliest_time"]
    assert codes(early, Constraints()) == []


def test_missing_required_course() -> None:
    assert codes(["QCS110-02", "QCS230-02", "QCS310-02", "QCS301-02"]) == ["missing_required_course"]


def test_credit_limit_exceeded() -> None:
    six = ["QCS110-02", "QCS230-02", "QCS240-02", "QCS301-02", "QCS310-02"]
    assert "credit_limit_exceeded" in codes(six)


def test_credit_minimum_not_met() -> None:
    assert codes(["QCS240-02"]) == ["credit_minimum_not_met"]


def test_invalid_schedule_entries() -> None:
    assert "invalid_schedule" in codes(["QCS240-02", "NOPE-01", "QCS230-02", "QCS301-02", "QCS110-02"])
    # Same course twice.
    assert "invalid_schedule" in codes(["QCS240-02", "QCS230-02", "QCS230-03", "QCS301-02", "QCS110-02"])
    # QCS 210 is already completed.
    assert "invalid_schedule" in codes(["QCS240-02", "QCS210-02", "QCS230-02", "QCS301-02"])


def test_all_reasons_are_collected() -> None:
    found = set(codes(["QCS340-01", "QCS110-01"]))
    assert {
        "unmet_prerequisite",
        "before_earliest_time",
        "missing_required_course",
        "credit_minimum_not_met",
    } <= found
