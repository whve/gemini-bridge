"""SQLite Database and State Persistence Layer for AutoPi (Strict WAL mode).

Persists goals, execution rounds, artifacts, boss interventions, and checkpoints.
Ensures zero data loss upon crash, unexpected exit, or restart.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class AutoPiDB:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_schema(self) -> None:
        with self.get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS master_goals (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active', -- active, completed, paused, aborted
                    progress_pct INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sub_tasks (
                    id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    task_type TEXT NOT NULL DEFAULT 'local_worker', -- local_worker, gemini_delegation
                    status TEXT NOT NULL DEFAULT 'pending', -- pending, running, completed, failed, refining
                    expected_artifacts TEXT DEFAULT '[]', -- JSON list of filenames
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (goal_id) REFERENCES master_goals(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS iterations (
                    id TEXT PRIMARY KEY,
                    sub_task_id TEXT NOT NULL,
                    round_num INTEGER NOT NULL,
                    director_directive TEXT NOT NULL,
                    worker_telemetry TEXT NOT NULL,
                    verdict TEXT NOT NULL, -- pass, refine, fail, aborted
                    duration_sec REAL NOT NULL DEFAULT 0.0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (sub_task_id) REFERENCES sub_tasks(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    iteration_id TEXT,
                    file_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    sha256 TEXT NOT NULL,
                    artifact_type TEXT NOT NULL DEFAULT 'file',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS boss_interventions (
                    id TEXT PRIMARY KEY,
                    instruction TEXT NOT NULL,
                    priority TEXT NOT NULL DEFAULT 'normal', -- normal, critical
                    consumed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS loop_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_sub_tasks_status ON sub_tasks(status);
                CREATE INDEX IF NOT EXISTS idx_sub_tasks_goal ON sub_tasks(goal_id);
                CREATE INDEX IF NOT EXISTS idx_iterations_task ON iterations(sub_task_id);
                """
            )

    # -------------------------------------------------------------------------
    # Master Goals
    # -------------------------------------------------------------------------
    def set_master_goal(self, goal_id: str, title: str, description: str, metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
        now = utc_now()
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO master_goals (id, title, description, status, progress_pct, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, 'active', 0, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    description=excluded.description,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at;
                """,
                (goal_id, title, description, json.dumps(metadata or {}), now, now),
            )
        return self.get_master_goal(goal_id)  # type: ignore

    def get_master_goal(self, goal_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM master_goals WHERE id = ?", (goal_id,)).fetchone()
            if row:
                d = dict(row)
                d["metadata"] = json.loads(d["metadata_json"] or "{}")
                return d
            return None

    def get_active_goal(self) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM master_goals WHERE status IN ('active', 'completed') ORDER BY updated_at DESC LIMIT 1").fetchone()
            if row:
                d = dict(row)
                d["metadata"] = json.loads(d["metadata_json"] or "{}")
                return d
            return None

    def update_goal_progress(self, goal_id: str, status: str, progress_pct: int) -> None:
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE master_goals SET status = ?, progress_pct = ?, updated_at = ? WHERE id = ?",
                (status, progress_pct, utc_now(), goal_id),
            )

    # -------------------------------------------------------------------------
    # Sub Tasks
    # -------------------------------------------------------------------------
    def add_sub_task(
        self,
        task_id: str,
        goal_id: str,
        seq: int,
        title: str,
        description: str,
        task_type: str = "local_worker",
        expected_artifacts: List[str] | None = None,
    ) -> Dict[str, Any]:
        now = utc_now()
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO sub_tasks (id, goal_id, seq, title, description, task_type, status, expected_artifacts, retry_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, 0, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    description=excluded.description,
                    task_type=excluded.task_type,
                    expected_artifacts=excluded.expected_artifacts,
                    updated_at=excluded.updated_at;
                """,
                (task_id, goal_id, seq, title, description, task_type, json.dumps(expected_artifacts or []), now, now),
            )
        return self.get_sub_task(task_id)  # type: ignore

    def get_sub_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM sub_tasks WHERE id = ?", (task_id,)).fetchone()
            if row:
                d = dict(row)
                d["expected_artifacts"] = json.loads(d["expected_artifacts"] or "[]")
                return d
            return None

    def list_sub_tasks(self, goal_id: str) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM sub_tasks WHERE goal_id = ? ORDER BY seq ASC", (goal_id,)).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["expected_artifacts"] = json.loads(d["expected_artifacts"] or "[]")
                out.append(d)
            return out

    def get_next_pending_task(self, goal_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM sub_tasks WHERE goal_id = ? AND status IN ('pending', 'running', 'refining') ORDER BY seq ASC LIMIT 1",
                (goal_id,),
            ).fetchone()
            if row:
                d = dict(row)
                d["expected_artifacts"] = json.loads(d["expected_artifacts"] or "[]")
                return d
            return None

    def update_task_status(self, task_id: str, status: str, last_error: str = "", increment_retry: bool = False) -> None:
        with self.get_connection() as conn:
            if increment_retry:
                conn.execute(
                    "UPDATE sub_tasks SET status = ?, last_error = ?, retry_count = retry_count + 1, updated_at = ? WHERE id = ?",
                    (status, last_error, utc_now(), task_id),
                )
            else:
                conn.execute(
                    "UPDATE sub_tasks SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
                    (status, last_error, utc_now(), task_id),
                )

    # -------------------------------------------------------------------------
    # Iterations & Telemetry
    # -------------------------------------------------------------------------
    def record_iteration(
        self,
        iteration_id: str,
        sub_task_id: str,
        round_num: int,
        director_directive: Dict[str, Any] | str,
        worker_telemetry: Dict[str, Any] | str,
        verdict: str,
        duration_sec: float,
    ) -> None:
        d_str = json.dumps(director_directive) if isinstance(director_directive, dict) else str(director_directive)
        w_str = json.dumps(worker_telemetry) if isinstance(worker_telemetry, dict) else str(worker_telemetry)
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO iterations (id, sub_task_id, round_num, director_directive, worker_telemetry, verdict, duration_sec, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (iteration_id, sub_task_id, round_num, d_str, w_str, verdict, duration_sec, utc_now()),
            )

    def list_recent_iterations(self, limit: int = 15) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM iterations ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["director_directive"] = json.loads(d["director_directive"])
                except Exception:
                    pass
                try:
                    d["worker_telemetry"] = json.loads(d["worker_telemetry"])
                except Exception:
                    pass
                out.append(d)
            return out

    # -------------------------------------------------------------------------
    # Artifacts
    # -------------------------------------------------------------------------
    def record_artifact(self, artifact_id: str, iteration_id: Optional[str], file_path: str, file_size: int, sha256: str, artifact_type: str) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO artifacts (id, iteration_id, file_path, file_size, sha256, artifact_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (artifact_id, iteration_id, file_path, file_size, sha256, artifact_type, utc_now()),
            )

    def list_artifacts(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM artifacts ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

    # -------------------------------------------------------------------------
    # Boss Interventions (Human Override)
    # -------------------------------------------------------------------------
    def inject_boss_intervention(self, intervention_id: str, instruction: str, priority: str = "normal") -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO boss_interventions (id, instruction, priority, consumed, created_at)
                VALUES (?, ?, ?, 0, ?);
                """,
                (intervention_id, instruction, priority, utc_now()),
            )

    def get_unconsumed_interventions(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM boss_interventions WHERE consumed = 0 ORDER BY priority DESC, created_at ASC").fetchall()
            return [dict(r) for r in rows]

    def mark_intervention_consumed(self, intervention_id: str) -> None:
        with self.get_connection() as conn:
            conn.execute("UPDATE boss_interventions SET consumed = 1 WHERE id = ?", (intervention_id,))

    # -------------------------------------------------------------------------
    # Global Loop State (Pause, Emergency Stop, Mode)
    # -------------------------------------------------------------------------
    def set_loop_state(self, key: str, value: str) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO loop_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at;
                """,
                (key, value, utc_now()),
            )

    def get_loop_state(self, key: str, default: str = "") -> str:
        with self.get_connection() as conn:
            row = conn.execute("SELECT value FROM loop_state WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default
