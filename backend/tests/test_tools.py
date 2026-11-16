from agent_replay.planner.tools import TOOLS, argument_errors, execute_tool, tool_schemas


def test_four_tools_with_schemas() -> None:
    assert set(TOOLS) == {
        "get_degree_requirements",
        "check_prerequisite",
        "search_sections",
        "build_schedule",
    }
    schemas = tool_schemas()
    assert all(s["type"] == "function" and s["function"]["parameters"]["type"] == "object" for s in schemas)


def test_get_degree_requirements() -> None:
    out = execute_tool("get_degree_requirements", {"student_id": "stu_01"})
    assert out["must_take_this_term"] == ["QCS 240"]
    assert "QCS 101" not in [c["course_code"] for c in out["remaining_required"]]
    assert "error" in execute_tool("get_degree_requirements", {"student_id": "nobody"})


def test_check_prerequisite() -> None:
    assert (
        execute_tool("check_prerequisite", {"student_id": "stu_01", "course_code": "QCS 240"})["met"] is True
    )
    out = execute_tool("check_prerequisite", {"student_id": "stu_01", "course_code": "QCS 340"})
    assert out == {"course_code": "QCS 340", "met": False, "missing": ["QCS 240"]}
    assert "error" in execute_tool("check_prerequisite", {"student_id": "stu_01", "course_code": "QCS 999"})


def test_search_sections_filters_by_time() -> None:
    every = execute_tool("search_sections", {"course_code": "QCS 240"})["sections"]
    late = execute_tool("search_sections", {"course_code": "QCS 240", "earliest_start": "10:00"})["sections"]
    assert len(every) == 2 and [s["section_id"] for s in late] == ["QCS240-02"]


def test_build_schedule_checks_only_existence_and_conflicts() -> None:
    ok = execute_tool("build_schedule", {"student_id": "stu_01", "section_ids": ["QCS340-02", "QCS240-02"]})
    # QCS 340 has an unmet prerequisite, and the tool does not care.
    assert ok["ok"] is True and ok["schedule"]["total_credits"] == 7
    clash = execute_tool(
        "build_schedule", {"student_id": "stu_01", "section_ids": ["QCS240-02", "QCS110-03"]}
    )
    assert clash["ok"] is False and clash["conflicts"] == [["QCS240-02", "QCS110-03"]]
    unknown = execute_tool("build_schedule", {"student_id": "stu_01", "section_ids": ["X-1"]})
    assert unknown["unknown_section_ids"] == ["X-1"]


def test_bad_calls_return_errors_instead_of_raising() -> None:
    assert execute_tool("drop_table", {})["error"].startswith("unknown tool")
    assert (
        execute_tool("search_sections", {"course_code": "QCS 240", "earliest_start": 1000})["error"]
        == "invalid arguments"
    )
    assert (
        execute_tool("search_sections", {"course_code": "QCS 240", "extra": 1})["error"]
        == "invalid arguments"
    )
    assert argument_errors("check_prerequisite", {"student_id": "stu_01"})
    assert argument_errors("check_prerequisite", {"student_id": "stu_01", "course_code": "QCS 240"}) == []
