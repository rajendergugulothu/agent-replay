# Security review

Reviewed: the whole repository on the `build/agent-replay` branch, before any deployment.

The app will be public at a showcase, holds a paid OpenAI API key on the server, and sends some visitor input to a model. The review covers secrets, cost abuse, prompt injection, input validation, web headers, dependencies, the container and data.

Status values: **Fixed** (changed in this branch, with a test where one is named), **Accepted** (a known limit, with the reason), **Open** (needs an action from you).

## Findings

| # | Severity | Where | Risk | Fix | Status |
|---|---|---|---|---|---|
| 1 | High | `backend/pyproject.toml` | The first set of pinned versions had 8 known vulnerabilities (7 in starlette, 1 in pytest), including request-handling flaws in the web framework. | Upgraded and re-pinned every dependency. `pip-audit` now reports none. | Fixed |
| 2 | High | `backend/agent_replay/planner/agent.py`, `localizers/llm_judge.py` | The per-run token budget is only checked between steps, so one model reply had no upper bound and could cost any amount. | Every model call now sends `max_completion_tokens` (env `MAX_COMPLETION_TOKENS`, default 4000). Tests: `test_every_model_call_carries_an_output_cap`, `test_experiment_passes_the_output_cap_to_agent_and_judge`. | Fixed |
| 3 | Medium | `backend/agent_replay/api/app.py:225` | The rate limiter keyed on the first `X-Forwarded-For` entry, which the client controls. A visitor could invent a new address per request and never be limited. | It now uses the last entry, which the Cloud Run proxy appends. Test: `test_rate_limit_ignores_client_supplied_forwarded_addresses`. | Fixed |
| 4 | Medium | `backend/agent_replay/planner/tools.py:23` | Edited tool arguments are placed in the model's context. A long string could carry instructions to the model (prompt injection). | String arguments are capped at 24 characters, types are strict, extra fields are rejected, and the edited call is validated against the tool schema before anything runs. Test: `test_localize_and_resume_validation`. | Fixed |
| 5 | Medium | `backend/agent_replay/api/app.py:140` | Interactive API docs and the OpenAPI schema were served in production, which hands an attacker the full API surface. | Disabled when `PRODUCTION=1`. Test: `test_production_hides_docs_and_sets_hsts_and_cors`. | Fixed |
| 6 | Medium | `backend/agent_replay/api/app.py:558` | `POST /api/reset` needs no login. Any visitor can clear every visitor's runs. | Rate limited, and it only deletes rows with `purpose = 'visitor'`, never experiment or seed data. A shared demo screen needs a one-key reset, so no login was added. | Accepted |
| 7 | Medium | `backend/agent_replay/api/app.py:234` | The daily token limit is checked when a run starts. Runs already in flight can take the total past the limit. | The overshoot is bounded: at most `MAX_CONCURRENT_RUNS` (2) runs of `MAX_TOKENS_PER_RUN` each. Set a hard monthly limit on the OpenAI account as the real backstop (see Open actions). | Accepted |
| 8 | Low | `backend/agent_replay/api/app.py:416` | Each open event stream holds a server thread for up to 5 minutes. Many open streams could slow the app for others. | Streams end when the run ends or after 5 minutes. Cloud Run concurrency is set to 40 per instance. No per-client cap on streams. | Accepted |
| 9 | Low | `backend/agent_replay/api/app.py:63` | The Content Security Policy allows inline styles (`style-src 'unsafe-inline'`), which React needs for computed sizes in the charts. | Scripts are restricted to same origin with no inline script and no eval. | Accepted |
| 10 | Low | `.github/workflows/*.yml`, `Dockerfile` | Actions are pinned by version tag and base images by tag, not by commit hash or digest. A compromised tag could change what runs. | Pin to hashes once the pipeline is stable. I could not look up the hashes from this environment. | Open |
| 11 | Low | `backend/agent_replay/api/app.py` (`get_run`) | Anyone who knows a run id can read that run. | Ids are random 128-bit values, validated by pattern, and there is no endpoint that lists them. Runs hold only synthetic data. | Accepted |
| 12 | Info | `backend/agent_replay/tracing.py` (`find_runs`, `insert`) | Two helpers build SQL from column or table names. | Names come only from code in this repository, never from a request, and `insert` checks the table against a fixed list. All values use bound parameters. | Accepted |

## What was checked, by area

**Secrets.** No key appears in code, tests, the Docker image definition or the frontend source. `.env` and database files are git-ignored, with one exception: `demo/seed.db`, the frozen experiment database that ships in the image. The API key is read from the environment on the server only and is never sent to the browser. Error responses carry a fixed message and a request id, never an exception or stack trace (test: `test_unhandled_error_returns_plain_500`, `test_failed_model_call_ends_the_run_without_leaking_detail`). Request logs hold method, path, status and duration, never bodies or prompts. The Docker build context excludes `.env`, `.git` and local data through `.dockerignore`.

**Cost abuse.** All limits are enforced on the server: per-address rate limit on every endpoint that spends tokens, two concurrent runs (HTTP 429 beyond that), `DAILY_TOKEN_LIMIT`, `MAX_STEPS_PER_RUN`, `MAX_TOKENS_PER_RUN`, and the new per-reply cap. The rule-based localizer costs nothing and stays available when the budget is used up. In `DEMO_MODE` only the scenarios in `DEMO_SCENARIOS` can be started.

**Prompt injection.** The only visitor-controlled text that reaches a model is a tool argument of at most 24 characters. The repair hint is built from a fixed template and takes no free text; the request model rejects unknown fields. The model can only reach the four planner tools, which read the synthetic catalog and write nothing. A call to any other tool name returns an error result. Visitor runs and their diagnoses are separate rows from the experiment's, and the results screen reads a frozen file that no request can change, so injected text cannot alter the stored experiment verdicts.

**Input validation.** Every request body is a Pydantic model that forbids extra fields. Run ids and scenario ids are validated by pattern. Scenario files are loaded only from the scenarios folder: the id pattern allows no dots or slashes (test: `test_scenario_path_rejects_traversal`, and the `../../etc/passwd` case in `test_bad_input_gets_a_clear_json_error`).

**Web.** No cross-origin access unless `ALLOWED_ORIGIN` is set, and then only that origin. Headers on every response: Content-Security-Policy, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and `Strict-Transport-Security` in production. Cloud Run serves `run.app` addresses over HTTPS.

**Dependencies.** Backend versions are pinned in `pyproject.toml` and frontend versions in `package.json` with a lockfile. `pip-audit` and `npm audit` run in CI on every pull request.

**Container.** Multi-stage build: Node and the build tools stay in the first stage. The final image is `python:3.12-slim` with the backend, the built frontend and read-only data. It runs as a non-root user (uid 10001). The compose file runs it with a read-only root filesystem, no Linux capabilities and no privilege escalation. No secret is copied into the image.

**Data.** No personal data is stored. Courses, sections and students are synthetic. Logs do not contain prompts.

## Scans

| Scan | Result |
|---|---|
| `pip-audit` on the backend | No known vulnerabilities after the upgrade in finding 1. |
| `npm audit` on the frontend | See the note under Open actions. |
| Secret scan of git history | gitleaks could not be installed in the build environment (downloading and running the binary was blocked). A pattern search of the full history and working tree for OpenAI, AWS, GitHub and Google key formats and private key headers found nothing. The gitleaks job in CI will run the real scan on the first pull request. |
| Docker image build | Not run: the build environment has no Docker daemon. The CI `image` job builds it and checks it runs as uid 10001. |

## Open actions for you

1. Set a hard monthly spending limit on the OpenAI account before the key goes into production (finding 7).
2. Check the first CI run: the gitleaks job, the `npm audit` step and the image build have not run anywhere yet.
3. Pin GitHub Actions and base images by hash once the pipeline is stable (finding 10).
4. Decide whether the unauthenticated reset is acceptable for the public URL, or only for the laptop at the event (finding 6). If not, the simplest change is to disable `/api/reset` when `PRODUCTION=1`.
