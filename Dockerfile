# Stage 1: build the frontend. Node and the build tools stay in this stage.
FROM node:22-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: the backend plus the built frontend, nothing else.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    PRODUCTION=1 \
    DEMO_MODE=1 \
    SCENARIOS_DIR=/app/scenarios \
    RESULTS_DIR=/app/results \
    RECORDINGS_DIR=/app/demo/recordings \
    FRONTEND_DIR=/app/frontend/dist \
    SEED_DATABASE_PATH=/app/demo/seed.db \
    DATABASE_PATH=/tmp/agent_replay.db

WORKDIR /app
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/agent_replay backend/agent_replay
RUN pip install ./backend && rm -rf backend

# Read-only assets: scenario files, the frozen experiment results, the seed
# database and the offline recordings. Visitor runs go to /tmp.
COPY scenarios scenarios
COPY results/frozen results/frozen
COPY demo demo
COPY --from=frontend /build/dist frontend/dist

RUN useradd --system --uid 10001 --no-create-home app
USER 10001

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/health', timeout=3)"

# One worker: SQLite has a single writer and the run limit is kept in memory.
CMD ["sh", "-c", "exec uvicorn agent_replay.api.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
