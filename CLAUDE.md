# Agent Replay

A research project with a working tool attached. We study whether finding the step where a tool-using AI agent went wrong leads to a successful, cheaper recovery than a full restart.

Main question: How does failure-localization accuracy affect the success, cost, and latency of checkpoint-based agent recovery?

## The system

- Agent under test: a semester planner agent over a SYNTHETIC course catalog. Four tools: get_degree_requirements, check_prerequisite, search_sections, build_schedule. A deterministic validator gives every final schedule a pass or fail.
- Trace recorder: saves every step (model input, tool name, arguments, output, tokens, seconds, state checkpoint, depends-on steps) to SQLite.
- Fault injection: forces a specific error at a specific step (for example, a wrong argument at step 3). Everything after that step runs for real.
- Localizers: rule-based, LLM judge, hybrid. Same interface, so they can be swapped.
- Recovery strategies: full restart; resume at the predicted step; resume at the true step (best-case ceiling).
- Experiment runner, FastAPI backend, React + TypeScript UI.

## Key definitions (do not blur these)

- Injected step: where the error was planted.
- Observed failure step: where the run visibly fails.
- Counterfactual outcome: success rate when only the injected step is fixed and the agent continues.
- Deterministic replay: reuses recorded outputs, no model calls. Only for viewing traces.
- Agent continuation: restores a checkpoint and lets the model act again. Used for all verification and recovery.
- Recovery cost = localization + repair + replay tokens, compared against a full restart. Verification replays count as localization.
- Full restart and resume-at-true-step are shared baselines: run once per scenario and trial, not once per localizer.

## Stack

- Python 3.12, FastAPI, Pydantic v2, sqlite3, OpenAI Python SDK, pytest
- React + TypeScript + Vite for the frontend
- Docker, one container that serves the API and the built frontend
- Config only through env vars: OPENAI_API_KEY, OPENAI_MODEL, JUDGE_MODEL, DATABASE_PATH, MAX_TOKENS_PER_RUN, MAX_STEPS_PER_RUN

## Repo layout

```
backend/agent_replay/{catalog, validator, planner, tracing, injection, localizers, recovery, experiments, api}
backend/tests/
frontend/
scenarios/   (one YAML file per failure scenario)
docs/
```

## Rules

- Research integrity: never hardcode, fake, or predetermine results. Every number shown anywhere must come from a stored run. The only scripted thing is the injected fault.
- Keep code simple and readable. Type hints everywhere. No extra layers of abstraction. Comments explain why, not what. No emoji, no banner comments.
- Write tests with every module. Mock the OpenAI client in unit tests so they are free and deterministic.
- Never commit secrets. .env stays in .gitignore. The API key is only ever used server side.
- Ask before adding a new dependency.
- If a requirement is unclear, ask instead of guessing.
- Commit in small steps with short, plain commit messages, using the repo's configured git identity (Rajender). Do not add AI author or co-author lines.
