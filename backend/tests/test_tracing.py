import sqlite3

import pytest
from fake_model import FakeClient

from agent_replay.injection import Scenario
from agent_replay.planner.agent import AgentSettings
from agent_replay.recovery import run_scenario
from agent_replay.tracing import TraceRecorder


def test_run_round_trips_through_sqlite(
    recorder: TraceRecorder, client: FakeClient, agent_settings: AgentSettings, scenario_01: Scenario
) -> None:
    run_id = run_scenario(client, recorder, scenario_01, agent_settings, purpose="test")
    # A second recorder on the same file sees the same run: nothing lives in memory.
    run = TraceRecorder(recorder.db_path).load_run(run_id)
    assert run.scenario_id == "scenario_01" and run.model == "fake-model"
    assert run.constraints.earliest_start == "10:00"
    first = run.step(1)
    assert first.tool_name == "get_degree_requirements"
    assert first.model_input == first.checkpoint.messages
    assert first.tool_output and first.tool_output["student_id"] == "stu_01"
    assert first.prompt_tokens > 0 and first.completion_tokens > 0
    assert run.final_schedule and run.created_at and run.ended_at


def test_missing_run_and_duplicate_step(
    recorder: TraceRecorder, client: FakeClient, agent_settings: AgentSettings, scenario_01: Scenario
) -> None:
    with pytest.raises(KeyError):
        recorder.load_run("nope")
    run = recorder.load_run(run_scenario(client, recorder, scenario_01, agent_settings, purpose="test"))
    with pytest.raises(sqlite3.IntegrityError):
        recorder.record_step(run.id, run.step(1))
    with pytest.raises(KeyError):
        run.step(99)


def test_find_runs_and_insert_guard(
    recorder: TraceRecorder, client: FakeClient, agent_settings: AgentSettings, scenario_01: Scenario
) -> None:
    run_id = run_scenario(client, recorder, scenario_01, agent_settings, purpose="test")
    assert [r.id for r in recorder.find_runs(scenario_id="scenario_01", kind="original")] == [run_id]
    assert recorder.find_runs(kind="full_restart") == []
    with pytest.raises(ValueError):
        recorder.insert("runs", {"x": 1})
