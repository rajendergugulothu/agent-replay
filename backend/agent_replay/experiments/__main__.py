"""Command line for the experiment.

python -m agent_replay.experiments label
python -m agent_replay.experiments run --experiment-id nov-final
python -m agent_replay.experiments export --experiment-id nov-final
python -m agent_replay.experiments freeze --experiment-id nov-final --version v1
"""

import argparse
import json
import shutil
import sys
from datetime import date

from agent_replay.config import Settings
from agent_replay.experiments.export import export_results, markdown_summary
from agent_replay.experiments.labeling import label_all
from agent_replay.experiments.runner import make_plan, run_experiment
from agent_replay.experiments.scoring import LocalizerScore, format_table
from agent_replay.injection import load_scenarios
from agent_replay.llm import make_client
from agent_replay.planner.agent import agent_settings_from
from agent_replay.tracing import TraceRecorder


def main() -> int:
    parser = argparse.ArgumentParser(prog="agent_replay.experiments")
    sub = parser.add_subparsers(dest="command", required=True)
    label = sub.add_parser("label", help="confirm scenario labels with counterfactual runs")
    label.add_argument("--trials", type=int, default=3)
    run = sub.add_parser("run", help="run the experiment, resuming where it stopped")
    run.add_argument("--experiment-id", required=True)
    run.add_argument("--trials", type=int, default=3)
    run.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    export = sub.add_parser("export", help="write results.json and results.csv")
    export.add_argument("--experiment-id", required=True)
    freeze = sub.add_parser("freeze", help="copy an experiment's results to results/frozen")
    freeze.add_argument("--experiment-id", required=True)
    freeze.add_argument("--version", required=True)
    sub.add_parser("table", help="print the frozen results as Markdown tables for the README")
    args = parser.parse_args()

    settings = Settings.from_env()
    recorder = TraceRecorder(settings.database_path)
    scenarios = load_scenarios(settings.scenarios_dir)
    out_dir = settings.results_dir / getattr(args, "experiment_id", "")

    if args.command == "table":
        frozen_file = settings.results_dir / "frozen" / "results.json"
        if not frozen_file.exists():
            print(
                f"{frozen_file} does not exist. Run the experiment, export and freeze first.", file=sys.stderr
            )
            return 1
        print(markdown_summary(json.loads(frozen_file.read_text())))
        return 0

    if args.command == "export":
        summary = export_results(recorder, args.experiment_id, out_dir)
        scores = {m: LocalizerScore.model_validate(s) for m, s in summary["by_localizer"].items()}
        print(format_table(scores) if scores else "No scored diagnoses yet.")
        print(f"Wrote {out_dir / 'results.json'} and {out_dir / 'results.csv'}")
        return 0

    if args.command == "freeze":
        source = out_dir / "results.json"
        if not source.exists():
            print(f"{source} does not exist. Run export first.", file=sys.stderr)
            return 1
        frozen = settings.results_dir / "frozen"
        frozen.mkdir(parents=True, exist_ok=True)
        summary = json.loads(source.read_text())
        summary["frozen"] = {"version": args.version, "frozen_on": date.today().isoformat()}
        (frozen / "results.json").write_text(json.dumps(summary, indent=2))
        shutil.copy(out_dir / "results.csv", frozen / "results.csv")
        print(f"Froze {args.experiment_id} as {args.version} in {frozen}")
        return 0

    settings.require_models()
    client = make_client(settings)
    agent = agent_settings_from(settings)
    if args.command == "label":
        label_all(client, recorder, scenarios, settings.scenarios_dir, agent, args.trials)
        return 0

    plan = make_plan(recorder, len(scenarios), args.trials)
    print(f"Planned for {plan.scenarios} scenarios x {plan.trials} trials:")
    print(f"  original runs              {plan.original_runs}")
    print(f"  full restarts (shared)     {plan.full_restarts}")
    print(f"  resumes at true step       {plan.resume_true_runs}")
    print(f"  resumes at predicted step  up to {plan.resume_predicted_runs_max}")
    print(f"  agent runs in total        up to {plan.agent_runs_max}")
    print(f"  diagnoses                  {plan.diagnoses} (up to {plan.judge_calls_max} judge calls)")
    print(f"  estimated tokens           up to {plan.estimated_tokens_max:,} ({plan.estimate_basis})")
    print("Finished pieces already in the store are skipped.")
    if not args.yes and input("Start? [y/N] ").strip().lower() != "y":
        print("Not started.")
        return 1
    stopped = run_experiment(
        client,
        recorder,
        scenarios,
        agent,
        experiment_id=args.experiment_id,
        judge_model=settings.judge_model,
        judge_temperature=settings.judge_temperature,
        max_completion_tokens=settings.max_completion_tokens,
        trials=args.trials,
    )
    if stopped:
        print(f"{len(stopped)} cells stopped on an error. Rerun the same command to finish them.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
