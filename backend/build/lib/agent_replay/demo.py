"""Showcase tooling: record demo scenarios for offline use, and pre-flight checks.

python -m agent_replay.demo record      # needs the API, run once before the event
python -m agent_replay.demo preflight   # prints a pass or fail list
"""

import argparse
import dataclasses
import json
import os
import sys
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_replay.config import Settings
from agent_replay.injection import load_scenario, scenario_path
from agent_replay.llm import PlaybackClient, RecordingClient, make_client
from agent_replay.localizers import diagnose, make_localizer
from agent_replay.planner.agent import agent_settings_from
from agent_replay.recovery import (
    build_repair_hint,
    continue_run,
    full_restart,
    resume_at_true_step,
    run_scenario,
)
from agent_replay.tracing import TraceRecorder

METHODS = ("rule", "llm", "hybrid")


def walk_demo_flow(
    client: Any, recorder: TraceRecorder, settings: Settings, scenario_id: str
) -> dict[str, str]:
    """Do everything a visitor can do with one scenario: run it, diagnose it
    with each localizer, resume with the fix and with each hint, and restart.
    Recording this walk is what makes the same clicks work offline."""
    scenario = load_scenario(scenario_path(settings.scenarios_dir, scenario_id))
    agent = agent_settings_from(settings)
    original = recorder.load_run(run_scenario(client, recorder, scenario, agent, purpose="demo"))
    outcome: dict[str, str] = {"original": original.status}
    hints: set[tuple[int, str]] = set()
    for method in METHODS:
        localizer = make_localizer(
            method, client, settings.judge_model, settings.judge_temperature, settings.max_completion_tokens
        )
        _, diagnosis = diagnose(recorder, original, localizer)
        outcome[method] = str(diagnosis.predicted_step)
        if diagnosis.predicted_step is not None:
            hints.add(
                (
                    diagnosis.predicted_step,
                    build_repair_hint(original.step(diagnosis.predicted_step), diagnosis.failure_type),
                )
            )
    if original.status == "failed":
        if original.injected_step is not None:
            fixed = resume_at_true_step(client, recorder, original, agent, purpose="demo")
            outcome["fix_at_true_step"] = recorder.load_run(fixed).status
        for step, hint in sorted(hints):
            run_id = continue_run(
                client,
                recorder,
                parent_run_id=original.id,
                step=step,
                mode="repair_hint",
                settings=agent,
                hint=hint,
                purpose="demo",
            )
            outcome[f"hint_at_step_{step}"] = recorder.load_run(run_id).status
        outcome["full_restart"] = recorder.load_run(
            full_restart(client, recorder, original.id, agent, purpose="demo")
        ).status
    return outcome


def record(settings: Settings, scenario_ids: list[str]) -> int:
    settings.require_models()
    client = RecordingClient(
        make_client(dataclasses.replace(settings, offline=False)), settings.recordings_dir
    )
    recorder = TraceRecorder(settings.database_path)
    for scenario_id in scenario_ids:
        outcome = walk_demo_flow(client, recorder, settings, scenario_id)
        print(f"{scenario_id}: {json.dumps(outcome)}")
    print(f"Recorded responses are in {settings.recordings_dir}")
    return 0


def _check_api_key(settings: Settings) -> str:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    settings.require_models()
    client = make_client(dataclasses.replace(settings, offline=False))
    for model in {settings.openai_model, settings.judge_model}:
        client.models.retrieve(model)
    return f"key accepted, models {settings.openai_model} and {settings.judge_model} exist"


def _check_limits(settings: Settings) -> str:
    missing = [
        n
        for n in ("MAX_TOKENS_PER_RUN", "MAX_STEPS_PER_RUN", "DAILY_TOKEN_LIMIT")
        if not os.environ.get(n, "").strip()
    ]
    if missing:
        raise RuntimeError(f"not set explicitly: {', '.join(missing)}")
    return (
        f"{settings.max_tokens_per_run} tokens and {settings.max_steps_per_run} steps per run, "
        f"{settings.daily_token_limit} tokens per day"
    )


def _check_demo_scenarios(settings: Settings) -> str:
    if not settings.demo_scenarios:
        raise RuntimeError("DEMO_SCENARIOS is empty")
    for scenario_id in settings.demo_scenarios:
        load_scenario(scenario_path(settings.scenarios_dir, scenario_id))
    return ", ".join(settings.demo_scenarios)


def _check_offline(settings: Settings) -> str:
    if not settings.demo_scenarios:
        raise RuntimeError("DEMO_SCENARIOS is empty")
    settings.require_models()
    with tempfile.TemporaryDirectory() as work:
        recorder = TraceRecorder(Path(work) / "preflight.db")
        for scenario_id in settings.demo_scenarios:
            # Any request without a recording raises, which fails this check.
            walk_demo_flow(PlaybackClient(settings.recordings_dir), recorder, settings, scenario_id)
    return f"{len(settings.demo_scenarios)} scenarios play back fully with no network"


def _check_results(settings: Settings) -> str:
    path = settings.results_dir / "frozen" / "results.json"
    if not path.is_file():
        raise RuntimeError(f"{path} does not exist")
    summary = json.loads(path.read_text())
    frozen = summary["frozen"]
    return f"{frozen['version']} frozen on {frozen['frozen_on']}, {summary['run_count']} runs"


def _check_production(settings: Settings) -> str:
    url = os.environ.get("PRODUCTION_URL", "").strip().rstrip("/")
    if not url.startswith("https://"):
        raise RuntimeError("PRODUCTION_URL is not set to an https address")
    with urllib.request.urlopen(f"{url}/api/health", timeout=15) as response:  # noqa: S310
        body = json.loads(response.read())
    if body.get("status") != "ok":
        raise RuntimeError(f"unexpected health response: {body}")
    return f"{url} is healthy, mode {body['mode']}, demo mode {body['demo_mode']}"


CHECKS: list[tuple[str, Callable[[Settings], str]]] = [
    ("API key works", _check_api_key),
    ("Token limits are set", _check_limits),
    ("Demo scenarios load", _check_demo_scenarios),
    ("Offline mode works", _check_offline),
    ("Frozen results are present", _check_results),
    ("Production URL is healthy", _check_production),
]


def preflight(settings: Settings) -> int:
    failed = 0
    for name, check in CHECKS:
        try:
            print(f"PASS  {name}: {check(settings)}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            # Only the error type and message. An exception from the SDK must
            # not be able to print request headers.
            print(f"FAIL  {name}: {type(exc).__name__}: {str(exc)[:200]}")
    print(f"\n{len(CHECKS) - failed} of {len(CHECKS)} checks passed.")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="agent_replay.demo")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="record the demo scenarios for offline use")
    rec.add_argument("scenario_ids", nargs="*", help="defaults to DEMO_SCENARIOS")
    sub.add_parser("preflight", help="check the demo is ready")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command == "preflight":
        return preflight(settings)
    ids = args.scenario_ids or list(settings.demo_scenarios)
    if not ids:
        print("Name the scenarios to record, or set DEMO_SCENARIOS.", file=sys.stderr)
        return 1
    return record(settings, ids)


if __name__ == "__main__":
    sys.exit(main())
