"""Diagnose a stored run.

python -m agent_replay.localize --run <run_id> --method rule|llm|hybrid
"""

import argparse
import sys

from agent_replay.config import Settings
from agent_replay.llm import make_client
from agent_replay.localizers import diagnose, make_localizer
from agent_replay.tracing import TraceRecorder


def main() -> int:
    parser = argparse.ArgumentParser(prog="agent_replay.localize")
    parser.add_argument("--run", required=True)
    parser.add_argument("--method", required=True, choices=["rule", "llm", "hybrid"])
    args = parser.parse_args()
    settings = Settings.from_env()
    recorder = TraceRecorder(settings.database_path)
    run = recorder.load_run(args.run)
    client = None
    if args.method != "rule":
        settings.require_models()
        client = make_client(settings)
    localizer = make_localizer(args.method, client, settings.judge_model, settings.judge_temperature)
    _, diagnosis = diagnose(recorder, run, localizer)
    print(f"Method: {diagnosis.method}")
    print(f"Predicted step: {diagnosis.predicted_step}   (injected step, for reference: {run.injected_step})")
    print(f"Failure type: {diagnosis.failure_type.value if diagnosis.failure_type else None}")
    print(f"Reason: {diagnosis.reason}")
    for item in diagnosis.evidence:
        print(
            f"  evidence: step {item.step_number} {item.field} {item.value if item.value is not None else ''} {item.note}"
        )
    print(f"Tokens: {diagnosis.tokens}   Seconds: {diagnosis.seconds:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
