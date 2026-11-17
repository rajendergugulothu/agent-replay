"""SQLite trace store. Every run, step, diagnosis and recovery is written here."""

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_replay.models import (
    Constraints,
    StepRecord,
    StoredRun,
    StoredStep,
    ValidationResult,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS scenarios (
    id                    TEXT PRIMARY KEY,
    title                 TEXT NOT NULL,
    failure_type          TEXT NOT NULL,
    yaml_sha256           TEXT NOT NULL,
    definition_json       TEXT NOT NULL,
    label_status          TEXT NOT NULL DEFAULT 'unlabeled',
    counterfactual_trials INTEGER,
    counterfactual_passes INTEGER,
    label_source_run_id   TEXT,
    label_detail_json     TEXT,
    labeled_at            TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id                     TEXT PRIMARY KEY,
    scenario_id            TEXT,
    kind                   TEXT NOT NULL,
    purpose                TEXT NOT NULL,
    experiment_id          TEXT,
    trial                  INTEGER,
    parent_run_id          TEXT,
    branch_step            INTEGER,
    continuation_mode      TEXT,
    repair_hint            TEXT,
    agent_name             TEXT NOT NULL,
    student_id             TEXT NOT NULL,
    user_request           TEXT NOT NULL,
    constraints_json       TEXT NOT NULL,
    model                  TEXT NOT NULL,
    temperature            REAL,
    status                 TEXT NOT NULL,
    stop_reason            TEXT,
    final_schedule_json    TEXT,
    validator_reasons_json TEXT,
    injected_step          INTEGER,
    observed_failure_step  INTEGER,
    unfired_injections     INTEGER NOT NULL DEFAULT 0,
    prompt_tokens          INTEGER NOT NULL DEFAULT 0,
    completion_tokens      INTEGER NOT NULL DEFAULT 0,
    total_seconds          REAL NOT NULL DEFAULT 0,
    created_at             TEXT NOT NULL,
    ended_at               TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_scenario ON runs(scenario_id, kind);
CREATE INDEX IF NOT EXISTS idx_runs_experiment ON runs(experiment_id, scenario_id, trial);
CREATE INDEX IF NOT EXISTS idx_runs_parent ON runs(parent_run_id);

CREATE TABLE IF NOT EXISTS steps (
    id                  TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES runs(id),
    step_number         INTEGER NOT NULL,
    model_input_json    TEXT NOT NULL,
    model_output_json   TEXT NOT NULL,
    tool_name           TEXT,
    tool_call_id        TEXT,
    tool_args_json      TEXT,
    tool_output_json    TEXT,
    prompt_tokens       INTEGER NOT NULL,
    completion_tokens   INTEGER NOT NULL,
    seconds             REAL NOT NULL,
    checkpoint_json     TEXT NOT NULL,
    depends_on_json     TEXT NOT NULL,
    injection_json      TEXT,
    origin              TEXT NOT NULL DEFAULT 'model',
    reused_from_step_id TEXT,
    UNIQUE (run_id, step_number)
);

CREATE TABLE IF NOT EXISTS diagnoses (
    id                  TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES runs(id),
    method              TEXT NOT NULL,
    predicted_step      INTEGER,
    failure_type        TEXT,
    reason              TEXT NOT NULL,
    evidence_json       TEXT NOT NULL,
    checks_passed_json  TEXT NOT NULL DEFAULT '[]',
    tokens              INTEGER NOT NULL,
    seconds             REAL NOT NULL,
    judge_model         TEXT,
    judge_temperature   REAL,
    raw_response        TEXT,
    used_llm            INTEGER NOT NULL,
    verification_run_id TEXT,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_diagnoses_run ON diagnoses(run_id, method);

CREATE TABLE IF NOT EXISTS recoveries (
    id                  TEXT PRIMARY KEY,
    experiment_id       TEXT,
    scenario_id         TEXT NOT NULL,
    trial               INTEGER,
    original_run_id     TEXT NOT NULL REFERENCES runs(id),
    strategy            TEXT NOT NULL,
    method              TEXT NOT NULL DEFAULT '',
    diagnosis_id        TEXT,
    recovery_run_id     TEXT,
    resume_step         INTEGER,
    task_success        INTEGER NOT NULL,
    localization_tokens INTEGER NOT NULL,
    repair_tokens       INTEGER NOT NULL,
    replay_tokens       INTEGER NOT NULL,
    total_tokens        INTEGER NOT NULL,
    seconds             REAL NOT NULL,
    UNIQUE (experiment_id, scenario_id, trial, strategy, method)
);

CREATE TABLE IF NOT EXISTS experiment_results (
    id            TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    group_kind    TEXT NOT NULL,
    group_key     TEXT NOT NULL,
    metric        TEXT NOT NULL,
    value         REAL,
    numerator     INTEGER,
    denominator   INTEGER,
    run_ids_json  TEXT NOT NULL,
    computed_at   TEXT NOT NULL,
    UNIQUE (experiment_id, group_kind, group_key, metric)
);
"""


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _loads(text: str | None) -> Any:
    return None if text is None else json.loads(text)


class TraceRecorder:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        # One short-lived connection per operation. API requests and background
        # runs live on different threads, and sqlite3 connections must not be shared.
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            with conn:
                yield conn
        finally:
            conn.close()

    def start_run(
        self,
        *,
        scenario_id: str | None,
        kind: str,
        purpose: str,
        student_id: str,
        user_request: str,
        constraints: Constraints,
        model: str,
        temperature: float | None,
        parent_run_id: str | None = None,
        branch_step: int | None = None,
        continuation_mode: str | None = None,
        repair_hint: str | None = None,
        experiment_id: str | None = None,
        trial: int | None = None,
        agent_name: str = "semester_planner",
    ) -> str:
        run_id = new_id()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO runs (id, scenario_id, kind, purpose, experiment_id, trial,
                       parent_run_id, branch_step, continuation_mode, repair_hint, agent_name,
                       student_id, user_request, constraints_json, model, temperature, status,
                       created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)""",
                (
                    run_id,
                    scenario_id,
                    kind,
                    purpose,
                    experiment_id,
                    trial,
                    parent_run_id,
                    branch_step,
                    continuation_mode,
                    repair_hint,
                    agent_name,
                    student_id,
                    user_request,
                    constraints.model_dump_json(),
                    model,
                    temperature,
                    now(),
                ),
            )
        return run_id

    def record_step(self, run_id: str, step: StepRecord, reused_from_step_id: str | None = None) -> str:
        step_id = new_id()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO steps (id, run_id, step_number, model_input_json,
                       model_output_json, tool_name, tool_call_id, tool_args_json,
                       tool_output_json, prompt_tokens, completion_tokens, seconds,
                       checkpoint_json, depends_on_json, injection_json, origin,
                       reused_from_step_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    step_id,
                    run_id,
                    step.step_number,
                    json.dumps(step.model_input),
                    json.dumps(step.model_output),
                    step.tool_name,
                    step.tool_call_id,
                    None if step.tool_args is None else json.dumps(step.tool_args),
                    None if step.tool_output is None else json.dumps(step.tool_output),
                    step.prompt_tokens,
                    step.completion_tokens,
                    step.seconds,
                    step.checkpoint.model_dump_json(),
                    json.dumps(step.depends_on),
                    None if step.injection is None else json.dumps(step.injection),
                    step.origin,
                    reused_from_step_id,
                ),
            )
        return step_id

    def copy_reused_steps(self, run_id: str, parent: StoredRun, up_to_step: int) -> None:
        """Copy the parent's steps 1..up_to_step so the new run reads as one
        complete trace. They keep their token counts for display, but only
        steps the model produced in this run count toward its totals."""
        for step in parent.steps:
            if step.step_number <= up_to_step:
                record = StepRecord(**step.model_dump(exclude={"id", "run_id", "reused_from_step_id"}))
                record.origin = "reused"
                self.record_step(run_id, record, reused_from_step_id=step.reused_from_step_id or step.id)

    def end_run(
        self,
        run_id: str,
        *,
        status: str,
        stop_reason: str,
        final_schedule: list[str] | None = None,
        validation: ValidationResult | None = None,
        observed_failure_step: int | None = None,
        unfired_injections: int = 0,
    ) -> None:
        with self.connect() as conn:
            totals = conn.execute(
                """SELECT COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0),
                          COALESCE(SUM(seconds), 0)
                   FROM steps WHERE run_id = ? AND origin != 'reused'""",
                (run_id,),
            ).fetchone()
            injected = conn.execute(
                """SELECT MIN(step_number) FROM steps
                   WHERE run_id = ? AND injection_json IS NOT NULL AND origin != 'reused'""",
                (run_id,),
            ).fetchone()[0]
            conn.execute(
                """UPDATE runs SET status = ?, stop_reason = ?, final_schedule_json = ?,
                       validator_reasons_json = ?, injected_step = ?, observed_failure_step = ?,
                       unfired_injections = ?, prompt_tokens = ?, completion_tokens = ?,
                       total_seconds = ?, ended_at = ?
                   WHERE id = ?""",
                (
                    status,
                    stop_reason,
                    None if final_schedule is None else json.dumps(final_schedule),
                    None if validation is None else validation.model_dump_json(),
                    injected,
                    observed_failure_step,
                    unfired_injections,
                    totals[0],
                    totals[1],
                    totals[2],
                    now(),
                    run_id,
                ),
            )

    def load_run(self, run_id: str) -> StoredRun:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(f"no run with id {run_id}")
            step_rows = conn.execute(
                "SELECT * FROM steps WHERE run_id = ? ORDER BY step_number", (run_id,)
            ).fetchall()
        return _run_from_row(row, [_step_from_row(r) for r in step_rows])

    def find_runs(self, **filters: Any) -> list[StoredRun]:
        """Runs matching exact column values, oldest first, without their steps."""
        where = " AND ".join(f"{column} IS ?" for column in filters) or "1"
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM runs WHERE {where} ORDER BY created_at, rowid",  # noqa: S608
                tuple(filters.values()),
            ).fetchall()
        return [_run_from_row(row, []) for row in rows]

    def insert(self, table: str, values: dict[str, Any]) -> str:
        """Insert one row into a non-run table and return its id."""
        if table not in {"diagnoses", "recoveries", "experiment_results"}:
            raise ValueError(f"unexpected table {table}")
        values = {"id": new_id(), **values}
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        with self.connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({marks})",  # noqa: S608
                tuple(values.values()),
            )
        return str(values["id"])

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self.connect() as conn:
            conn.execute(sql, params)


def _step_from_row(row: sqlite3.Row) -> StoredStep:
    return StoredStep(
        id=row["id"],
        run_id=row["run_id"],
        step_number=row["step_number"],
        model_input=json.loads(row["model_input_json"]),
        model_output=json.loads(row["model_output_json"]),
        tool_name=row["tool_name"],
        tool_call_id=row["tool_call_id"],
        tool_args=_loads(row["tool_args_json"]),
        tool_output=_loads(row["tool_output_json"]),
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
        seconds=row["seconds"],
        checkpoint=json.loads(row["checkpoint_json"]),
        depends_on=json.loads(row["depends_on_json"]),
        injection=_loads(row["injection_json"]),
        origin=row["origin"],
        reused_from_step_id=row["reused_from_step_id"],
    )


def _run_from_row(row: sqlite3.Row, steps: list[StoredStep]) -> StoredRun:
    validation = _loads(row["validator_reasons_json"])
    return StoredRun(
        id=row["id"],
        scenario_id=row["scenario_id"],
        kind=row["kind"],
        purpose=row["purpose"],
        experiment_id=row["experiment_id"],
        trial=row["trial"],
        parent_run_id=row["parent_run_id"],
        branch_step=row["branch_step"],
        continuation_mode=row["continuation_mode"],
        repair_hint=row["repair_hint"],
        agent_name=row["agent_name"],
        student_id=row["student_id"],
        user_request=row["user_request"],
        constraints=json.loads(row["constraints_json"]),
        model=row["model"],
        temperature=row["temperature"],
        status=row["status"],
        stop_reason=row["stop_reason"],
        final_schedule=_loads(row["final_schedule_json"]),
        validation=validation,
        injected_step=row["injected_step"],
        observed_failure_step=row["observed_failure_step"],
        unfired_injections=row["unfired_injections"],
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
        total_seconds=row["total_seconds"],
        created_at=row["created_at"],
        ended_at=row["ended_at"],
        steps=steps,
    )
