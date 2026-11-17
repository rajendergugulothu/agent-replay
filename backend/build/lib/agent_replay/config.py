"""Settings read from environment variables. Nothing else configures the app."""

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _float_or_none(name: str, default: float | None) -> float | None:
    raw = os.environ.get(name)
    if raw is None:
        return default
    raw = raw.strip()
    return float(raw) if raw else None


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    openai_model: str
    judge_model: str
    database_path: Path
    max_tokens_per_run: int
    max_steps_per_run: int
    daily_token_limit: int
    max_completion_tokens: int
    agent_temperature: float | None
    judge_temperature: float | None
    demo_mode: bool
    offline: bool
    production: bool
    scenarios_dir: Path
    results_dir: Path
    recordings_dir: Path
    frontend_dir: Path
    seed_database_path: Path
    max_concurrent_runs: int
    rate_limit_per_minute: int
    allowed_origin: str
    demo_scenarios: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
            openai_model=os.environ.get("OPENAI_MODEL", "").strip(),
            judge_model=os.environ.get("JUDGE_MODEL", "").strip(),
            database_path=_path("DATABASE_PATH", REPO_ROOT / "data" / "agent_replay.db"),
            max_tokens_per_run=_int("MAX_TOKENS_PER_RUN", 40_000),
            max_steps_per_run=_int("MAX_STEPS_PER_RUN", 15),
            daily_token_limit=_int("DAILY_TOKEN_LIMIT", 2_000_000),
            max_completion_tokens=_int("MAX_COMPLETION_TOKENS", 4_000),
            agent_temperature=_float_or_none("AGENT_TEMPERATURE", None),
            judge_temperature=_float_or_none("JUDGE_TEMPERATURE", 0.0),
            demo_mode=_flag("DEMO_MODE"),
            offline=_flag("OFFLINE"),
            production=_flag("PRODUCTION"),
            scenarios_dir=_path("SCENARIOS_DIR", REPO_ROOT / "scenarios"),
            results_dir=_path("RESULTS_DIR", REPO_ROOT / "results"),
            recordings_dir=_path("RECORDINGS_DIR", REPO_ROOT / "demo" / "recordings"),
            frontend_dir=_path("FRONTEND_DIR", REPO_ROOT / "frontend" / "dist"),
            seed_database_path=_path("SEED_DATABASE_PATH", REPO_ROOT / "demo" / "seed.db"),
            max_concurrent_runs=_int("MAX_CONCURRENT_RUNS", 2),
            rate_limit_per_minute=_int("RATE_LIMIT_PER_MINUTE", 30),
            allowed_origin=os.environ.get("ALLOWED_ORIGIN", "").strip(),
            demo_scenarios=tuple(
                s.strip() for s in os.environ.get("DEMO_SCENARIOS", "").split(",") if s.strip()
            ),
        )

    def require_models(self) -> None:
        """Fail early with a plain message instead of an API error mid-run."""
        missing = [
            name
            for name, value in (("OPENAI_MODEL", self.openai_model), ("JUDGE_MODEL", self.judge_model))
            if not value
        ]
        if missing:
            raise RuntimeError(f"Set {' and '.join(missing)} in the environment.")
