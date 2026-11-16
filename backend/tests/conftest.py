from pathlib import Path

import pytest
from fake_model import FakeClient

from agent_replay.injection import Scenario, load_scenario
from agent_replay.planner.agent import AgentSettings
from agent_replay.tracing import TraceRecorder

SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "scenarios"


@pytest.fixture
def recorder(tmp_path: Path) -> TraceRecorder:
    return TraceRecorder(tmp_path / "test.db")


@pytest.fixture
def agent_settings() -> AgentSettings:
    return AgentSettings(model="fake-model", temperature=None, max_steps=15, max_tokens=40_000)


@pytest.fixture
def client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def scenario_01() -> Scenario:
    return load_scenario(SCENARIOS_DIR / "scenario_01.yaml")
