# One place for the commands used in development, CI and on showcase day.
PY := backend/.venv/bin/python
# Loads .env into the environment of a recipe line when the file exists.
ENV := set -a; [ -f .env ] && . ./.env; set +a;
DB ?= data/agent_replay.db

.PHONY: install lint typecheck test test-backend test-frontend test-e2e live-smoke \
        serve label experiment export freeze record preflight audit docker

install:
	cd backend && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
	cd frontend && npm ci

lint:
	cd backend && .venv/bin/ruff check agent_replay tests && .venv/bin/ruff format --check agent_replay tests
	cd frontend && npm run lint

typecheck:
	cd backend && .venv/bin/mypy agent_replay
	cd frontend && npm run typecheck

test-backend:
	cd backend && .venv/bin/pytest -q

test-frontend:
	cd frontend && npm run test

# The browser test drives the built frontend served by the backend with the
# fake model, so it needs a build first and no API key.
test-e2e:
	cd frontend && npm run build && npm run e2e -- --trace off

test: test-backend test-frontend test-e2e

# Costs tokens. Needs OPENAI_API_KEY, OPENAI_MODEL and JUDGE_MODEL.
live-smoke:
	$(ENV) cd backend && .venv/bin/pytest -q -m live

serve:
	$(ENV) cd backend && .venv/bin/uvicorn agent_replay.api.main:app --port 8000

label:
	$(ENV) $(PY) -m agent_replay.experiments label

# make experiment ID=nov-final
experiment:
	$(ENV) $(PY) -m agent_replay.experiments run --experiment-id $(ID)

export:
	$(ENV) $(PY) -m agent_replay.experiments export --experiment-id $(ID)

# make freeze ID=nov-final VERSION=v1
# Copies the results and the database into the places the image is built from.
freeze:
	$(ENV) $(PY) -m agent_replay.experiments freeze --experiment-id $(ID) --version $(VERSION)
	mkdir -p demo
	cp $(DB) demo/seed.db

record:
	$(ENV) $(PY) -m agent_replay.demo record

preflight:
	$(ENV) $(PY) -m agent_replay.demo preflight

audit:
	cd backend && .venv/bin/pip-audit --skip-editable
	cd frontend && npm audit --audit-level=high

docker:
	docker build -t agent-replay .
