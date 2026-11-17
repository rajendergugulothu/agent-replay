import json
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import SCENARIOS_DIR
from fake_model import FakeClient

from agent_replay import demo, localize, run
from agent_replay.config import Settings
from agent_replay.experiments import __main__ as experiments_cli
from agent_replay.tracing import TraceRecorder


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    values = {
        "OPENAI_MODEL": "fake-model",
        "JUDGE_MODEL": "fake-judge",
        "DATABASE_PATH": str(tmp_path / "cli.db"),
        "RESULTS_DIR": str(tmp_path / "results"),
        "RECORDINGS_DIR": str(tmp_path / "rec"),
        "SCENARIOS_DIR": str(SCENARIOS_DIR),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    for name in ("OPENAI_API_KEY", "OFFLINE", "DEMO_MODE", "DEMO_SCENARIOS", "AGENT_TEMPERATURE"):
        monkeypatch.delenv(name, raising=False)
    for module in (run, localize, experiments_cli, demo):
        monkeypatch.setattr(module, "make_client", lambda *args, **kwargs: FakeClient())
    return tmp_path


def call(monkeypatch: pytest.MonkeyPatch, main: Any, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["prog", *argv])
    return int(main())


def test_settings_come_only_from_the_environment(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_STEPS_PER_RUN", "9")
    monkeypatch.setenv("AGENT_TEMPERATURE", "0.2")
    monkeypatch.setenv("JUDGE_TEMPERATURE", "")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DEMO_SCENARIOS", "scenario_01, scenario_04,")
    settings = Settings.from_env()
    assert settings.max_steps_per_run == 9 and settings.max_tokens_per_run == 40_000
    assert settings.agent_temperature == 0.2 and settings.judge_temperature is None
    assert settings.demo_mode and not settings.offline
    assert settings.demo_scenarios == ("scenario_01", "scenario_04")
    assert settings.database_path == env / "cli.db"
    monkeypatch.setenv("JUDGE_MODEL", "")
    with pytest.raises(RuntimeError, match="JUDGE_MODEL"):
        Settings.from_env().require_models()


def test_run_and_localize_commands(env: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    assert call(monkeypatch, run.main, "--scenario", str(SCENARIOS_DIR / "scenario_01.yaml")) == 0
    out = capsys.readouterr().out
    assert "Step 3: check_prerequisite" in out and "[fault injected]" in out
    assert '"course_code": "QCS 340"' in out and "validator: unmet_prerequisite" in out
    assert "Status: failed (finished)" in out and "Injected step: 3" in out
    run_id = TraceRecorder(env / "cli.db").find_runs(kind="original")[0].id

    assert call(monkeypatch, localize.main, "--run", run_id, "--method", "rule") == 0
    assert "Predicted step: None" in capsys.readouterr().out
    assert call(monkeypatch, localize.main, "--run", run_id, "--method", "hybrid") == 0
    out = capsys.readouterr().out
    assert "Predicted step: 3" in out and "evidence: step 3" in out


def test_experiment_commands_end_to_end(env: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    assert call(monkeypatch, experiments_cli.main, "label", "--trials", "1") == 0
    assert "scenario_01: confirmed" in capsys.readouterr().out

    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert call(monkeypatch, experiments_cli.main, "run", "--experiment-id", "t1", "--trials", "1") == 1
    out = capsys.readouterr().out
    assert "agent runs in total        up to 60" in out and "Not started." in out

    assert (
        call(monkeypatch, experiments_cli.main, "run", "--experiment-id", "t1", "--trials", "1", "--yes") == 0
    )
    capsys.readouterr()
    assert call(monkeypatch, experiments_cli.main, "freeze", "--experiment-id", "t1", "--version", "v0") == 1
    assert call(monkeypatch, experiments_cli.main, "export", "--experiment-id", "t1") == 0
    assert "exact step" in capsys.readouterr().out
    assert call(monkeypatch, experiments_cli.main, "freeze", "--experiment-id", "t1", "--version", "v0") == 0
    frozen = json.loads((env / "results" / "frozen" / "results.json").read_text())
    assert frozen["frozen"]["version"] == "v0" and frozen["experiment_id"] == "t1"
    assert (env / "results" / "frozen" / "results.csv").is_file()


def test_demo_record_command(env: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    assert call(monkeypatch, demo.main, "record") == 1
    assert call(monkeypatch, demo.main, "record", "scenario_01") == 0
    assert '"original": "failed"' in capsys.readouterr().out
    assert list((env / "rec").glob("*.json"))
