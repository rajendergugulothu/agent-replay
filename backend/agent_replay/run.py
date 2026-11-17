"""Run one scenario and print its trace.

python -m agent_replay.run --scenario scenarios/scenario_01.yaml
"""

import argparse
import json
import sys
from pathlib import Path

from agent_replay.config import Settings
from agent_replay.injection import load_scenario
from agent_replay.llm import make_client
from agent_replay.models import StoredRun
from agent_replay.planner.agent import agent_settings_from
from agent_replay.recovery import run_scenario
from agent_replay.tracing import TraceRecorder


def format_trace(run: StoredRun) -> str:
    lines = [f"Run {run.id}  model {run.model}", f"Request: {run.user_request}", ""]
    for step in run.steps:
        if step.tool_name is None:
            lines.append(f"Step {step.step_number}: final answer: {step.model_output.get('content')}")
            continue
        mark = "  [fault injected]" if step.injection else ""
        lines.append(f"Step {step.step_number}: {step.tool_name}({json.dumps(step.tool_args)}){mark}")
        for log in step.injection or []:
            lines.append(f"    original: {json.dumps(log['original'])}")
            lines.append(f"    injected: {json.dumps(log['injected'])}")
        lines.append(f"    -> {json.dumps(step.tool_output)[:300]}")
    lines.append("")
    lines.append(f"Status: {run.status} ({run.stop_reason})")
    lines.append(f"Injected step: {run.injected_step}   Observed failure step: {run.observed_failure_step}")
    if run.unfired_injections:
        lines.append(f"Faults not delivered: {run.unfired_injections} (the targeted call never happened)")
    for reason in run.validation.reasons if run.validation else []:
        lines.append(f"  validator: {reason.code}: {reason.message}")
    lines.append(f"Tokens: {run.total_tokens}   Seconds: {run.total_seconds:.1f}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(prog="agent_replay.run")
    parser.add_argument("--scenario", required=True, type=Path)
    args = parser.parse_args()
    settings = Settings.from_env()
    settings.require_models()
    recorder = TraceRecorder(settings.database_path)
    run_id = run_scenario(
        make_client(settings),
        recorder,
        load_scenario(args.scenario),
        agent_settings_from(settings),
        purpose="cli",
    )
    print(format_trace(recorder.load_run(run_id)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
