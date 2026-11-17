"""One real scenario end to end against the real OpenAI API.

Costs tokens, so it only runs on request:  pytest -m live
With SMOKE_URL set it instead drives a deployed service over HTTP.
"""

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from conftest import SCENARIOS_DIR

from agent_replay.config import Settings
from agent_replay.injection import load_scenario
from agent_replay.llm import make_client
from agent_replay.localizers import diagnose, make_localizer
from agent_replay.planner.agent import agent_settings_from
from agent_replay.recovery import full_restart, replay_deterministic, run_scenario
from agent_replay.tracing import TraceRecorder

pytestmark = pytest.mark.live


def test_one_scenario_against_the_real_api(tmp_path: Path) -> None:
    if os.environ.get("SMOKE_URL"):
        pytest.skip("SMOKE_URL is set, running the deployed-service test instead")
    settings = Settings.from_env()
    settings.require_models()
    client = make_client(settings)
    recorder = TraceRecorder(tmp_path / "live.db")
    agent = agent_settings_from(settings)
    scenario = load_scenario(SCENARIOS_DIR / "scenario_01.yaml")
    run = recorder.load_run(run_scenario(client, recorder, scenario, agent, purpose="test"))

    # What the model does is its own business. What must hold is that the run
    # finished, was recorded completely, and used real token counts.
    assert run.status in {"passed", "failed"}
    assert run.total_tokens > 0 and all(s.prompt_tokens > 0 for s in run.steps)
    assert replay_deterministic(recorder, run.id).matches
    for method in ("rule", "llm", "hybrid"):
        _, diagnosis = diagnose(
            recorder, run, make_localizer(method, client, settings.judge_model, settings.judge_temperature)
        )
        assert diagnosis.reason
    restart = recorder.load_run(full_restart(client, recorder, run.id, agent, purpose="test"))
    assert restart.status in {"passed", "failed"}


def _call(base: str, path: str, body: dict[str, Any] | None = None) -> Any:
    request = urllib.request.Request(  # noqa: S310
        f"{base}{path}",
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="GET" if body is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.loads(response.read())


def test_deployed_service() -> None:
    base = os.environ.get("SMOKE_URL", "").rstrip("/")
    if not base:
        pytest.skip("SMOKE_URL is not set")
    assert _call(base, "/api/health")["status"] == "ok"
    scenario_id = _call(base, "/api/scenarios")[0]["id"]
    run_id = _call(base, "/api/runs", {"scenario_id": scenario_id})["run_id"]
    run: dict[str, Any] = {}
    for _ in range(120):
        run = _call(base, f"/api/runs/{run_id}")
        if run["status"] != "running":
            break
        time.sleep(2)
    assert run["status"] in {"passed", "failed"}, run["stop_reason"]
    assert _call(base, f"/api/runs/{run_id}/localize", {"method": "rule"})["method"] == "rule"
