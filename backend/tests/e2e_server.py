"""Start the real app with the fake model, for browser tests and UI work.

    python tests/e2e_server.py [port]

Everything it shows comes from the deterministic fake in fake_model.py and a
throwaway database. None of it is experiment data.
"""

import dataclasses
import json
import sys
import tempfile
from pathlib import Path

import uvicorn
from fake_model import FakeClient

from agent_replay.api.app import create_app
from agent_replay.config import REPO_ROOT, Settings
from agent_replay.experiments.export import export_results
from agent_replay.experiments.labeling import label_all
from agent_replay.experiments.runner import run_experiment
from agent_replay.injection import load_scenarios
from agent_replay.planner.agent import AgentSettings
from agent_replay.tracing import TraceRecorder


def build_settings(work: Path) -> Settings:
    return dataclasses.replace(
        Settings.from_env(),
        openai_model="fake-model",
        judge_model="fake-judge",
        database_path=work / "e2e.db",
        results_dir=work / "results",
        seed_database_path=work / "none.db",
        frontend_dir=REPO_ROOT / "frontend" / "dist",
        scenarios_dir=REPO_ROOT / "scenarios",
        rate_limit_per_minute=10_000,
        demo_mode=False,
        offline=False,
        production=False,
    )


def seed_results(settings: Settings) -> None:
    """Run a small experiment with the fake so the results screen has a real
    file in the real format to render."""
    recorder = TraceRecorder(settings.database_path)
    scenarios = load_scenarios(settings.scenarios_dir)
    agent = AgentSettings("fake-model", None, settings.max_steps_per_run, settings.max_tokens_per_run)
    label_all(FakeClient(), recorder, scenarios, settings.scenarios_dir, agent, log=lambda _: None)
    run_experiment(
        FakeClient(),
        recorder,
        scenarios,
        agent,
        experiment_id="fake-e2e",
        judge_model="fake-judge",
        judge_temperature=0.0,
        trials=2,
        log=lambda _: None,
    )
    summary = export_results(recorder, "fake-e2e", settings.results_dir / "fake-e2e")
    summary["frozen"] = {"version": "fake-model test data", "frozen_on": "2026-01-01"}
    frozen = settings.results_dir / "frozen"
    frozen.mkdir(parents=True, exist_ok=True)
    (frozen / "results.json").write_text(json.dumps(summary))


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8010
    settings = build_settings(Path(tempfile.mkdtemp(prefix="agent-replay-e2e-")))
    seed_results(settings)
    uvicorn.run(create_app(settings, FakeClient()), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
