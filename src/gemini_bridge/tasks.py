"""SQLite task persistence and unified task lifecycle management for Gemini Bridge."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import redact_sensitive


class TaskMode(str, Enum):
    DEEP_RESEARCH = "deep-research"
    APP_TASK = "app-task"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_USER_ACTION = "needs_user_action"
    CANCELLED = "cancelled"


class DetectionState(str, Enum):
    VERIFIED = "verified"
    DOM_CHANGED_OR_UNPARSEABLE = "page_detection_failed"
    UNKNOWN = "status_unknown"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_prompt_hash(mode: str, prompt: str) -> str:
    normalized = f"{mode.strip().lower()}::{prompt.strip()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def generate_task_id(mode: str) -> str:
    prefix = "dr" if mode == TaskMode.DEEP_RESEARCH.value else "at"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    short_uuid = uuid.uuid4().hex[:6]
    return f"{prefix}-{ts}-{short_uuid}"


@dataclass
class SourceItem:
    title: str
    url: str
    snippet: Optional[str] = None
    citation_index: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "citation_index": self.citation_index,
        }


@dataclass
class TaskRecord:
    task_id: str
    mode: str
    status: str
    backend: str
    prompt: str
    prompt_hash: str
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    prompt_file: Optional[str] = None
    result_dir: Optional[str] = None
    report_path: Optional[str] = None
    metadata_path: Optional[str] = None
    sources_path: Optional[str] = None
    log_path: Optional[str] = None
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    worker_pid: Optional[int] = None
    detection_state: str = DetectionState.VERIFIED.value
    error_message: Optional[str] = None
    user_action_hint: Optional[str] = None
    sources: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return data


class DuplicateTaskError(RuntimeError):
    """Raised when an identical prompt+mode task is already queued or running."""

    def __init__(self, existing_task_id: str, status: str):
        super().__init__(
            f"Duplicate active task detected: task '{existing_task_id}' with identical prompt and mode is currently '{status}'. "
            f"Use --force to submit anyway, or check status with `gemini-bridge status {existing_task_id}`."
        )
        self.existing_task_id = existing_task_id
        self.status = status


class TaskStore:
    """SQLite-backed task repository supporting concurrent reads/writes across processes."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    prompt_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    prompt_file TEXT,
                    result_dir TEXT,
                    report_path TEXT,
                    metadata_path TEXT,
                    sources_path TEXT,
                    log_path TEXT,
                    external_id TEXT,
                    external_url TEXT,
                    worker_pid INTEGER,
                    detection_state TEXT NOT NULL DEFAULT 'verified',
                    error_message TEXT,
                    user_action_hint TEXT,
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    extra_json TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_prompt_hash ON tasks(prompt_hash);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC);")

    def _row_to_record(self, row: sqlite3.Row) -> TaskRecord:
        sources = []
        extra = {}
        try:
            sources = json.loads(row["sources_json"] or "[]")
        except Exception:
            sources = []
        try:
            extra = json.loads(row["extra_json"] or "{}")
        except Exception:
            extra = {}

        return TaskRecord(
            task_id=row["task_id"],
            mode=row["mode"],
            status=row["status"],
            backend=row["backend"],
            prompt=row["prompt"],
            prompt_hash=row["prompt_hash"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            prompt_file=row["prompt_file"],
            result_dir=row["result_dir"],
            report_path=row["report_path"],
            metadata_path=row["metadata_path"],
            sources_path=row["sources_path"],
            log_path=row["log_path"],
            external_id=row["external_id"],
            external_url=row["external_url"],
            worker_pid=row["worker_pid"],
            detection_state=row["detection_state"],
            error_message=row["error_message"],
            user_action_hint=row["user_action_hint"],
            sources=sources,
            extra=extra,
        )

    def find_active_duplicate(self, mode: str, prompt: str) -> Optional[TaskRecord]:
        p_hash = compute_prompt_hash(mode, prompt)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM tasks
                WHERE prompt_hash = ? AND mode = ? AND status IN (?, ?, ?)
                ORDER BY created_at DESC LIMIT 1
                """,
                (
                    p_hash,
                    mode,
                    TaskStatus.QUEUED.value,
                    TaskStatus.RUNNING.value,
                    TaskStatus.NEEDS_USER_ACTION.value,
                ),
            ).fetchone()
            return self._row_to_record(row) if row else None

    def create_task(
        self,
        mode: str,
        prompt: str,
        backend: str,
        result_dir: Path,
        log_path: Path,
        prompt_file: Optional[str] = None,
        force: bool = False,
        extra: Optional[Dict[str, Any]] = None,
    ) -> TaskRecord:
        if mode not in (TaskMode.DEEP_RESEARCH.value, TaskMode.APP_TASK.value):
            raise ValueError(f"Unsupported task mode: {mode}. Must be 'deep-research' or 'app-task'.")
        if not prompt or not prompt.strip():
            raise ValueError("Task prompt cannot be empty.")

        if not force:
            dup = self.find_active_duplicate(mode, prompt)
            if dup is not None:
                raise DuplicateTaskError(dup.task_id, dup.status)

        task_id = generate_task_id(mode)
        now = utc_now_iso()
        p_hash = compute_prompt_hash(mode, prompt)
        task_result_dir = (Path(result_dir) / task_id).resolve()
        task_result_dir.mkdir(parents=True, exist_ok=True)

        report_path = task_result_dir / "report.md"
        metadata_path = task_result_dir / "metadata.json"
        sources_path = task_result_dir / "sources.md"
        task_log_path = task_result_dir / "task.log"

        record = TaskRecord(
            task_id=task_id,
            mode=mode,
            status=TaskStatus.QUEUED.value,
            backend=backend,
            prompt=prompt.strip(),
            prompt_hash=p_hash,
            created_at=now,
            updated_at=now,
            prompt_file=prompt_file,
            result_dir=str(task_result_dir),
            report_path=str(report_path),
            metadata_path=str(metadata_path),
            sources_path=str(sources_path),
            log_path=str(task_log_path),
            detection_state=DetectionState.VERIFIED.value,
            extra=extra or {"central_log_path": str(Path(log_path).resolve())},
        )

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    task_id, mode, status, backend, prompt, prompt_hash,
                    created_at, updated_at, started_at, completed_at,
                    prompt_file, result_dir, report_path, metadata_path,
                    sources_path, log_path, external_id, external_url,
                    worker_pid, detection_state, error_message, user_action_hint,
                    sources_json, extra_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.task_id,
                    record.mode,
                    record.status,
                    record.backend,
                    record.prompt,
                    record.prompt_hash,
                    record.created_at,
                    record.updated_at,
                    record.started_at,
                    record.completed_at,
                    record.prompt_file,
                    record.result_dir,
                    record.report_path,
                    record.metadata_path,
                    record.sources_path,
                    record.log_path,
                    record.external_id,
                    record.external_url,
                    record.worker_pid,
                    record.detection_state,
                    record.error_message,
                    record.user_action_hint,
                    json.dumps(record.sources, ensure_ascii=False),
                    json.dumps(record.extra, ensure_ascii=False),
                ),
            )
        return record

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            return self._row_to_record(row) if row else None

    def list_tasks(
        self,
        mode: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[TaskRecord]:
        query = "SELECT * FROM tasks WHERE 1=1"
        params: List[Any] = []
        if mode:
            query += " AND mode = ?"
            params.append(mode)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_record(r) for r in rows]

    def update_task(
        self,
        task_id: str,
        *,
        status: Optional[str] = None,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
        external_id: Optional[str] = None,
        external_url: Optional[str] = None,
        worker_pid: Optional[int] = None,
        detection_state: Optional[str] = None,
        error_message: Optional[str] = None,
        user_action_hint: Optional[str] = None,
        sources: Optional[List[Dict[str, Any]]] = None,
        extra_updates: Optional[Dict[str, Any]] = None,
        clear_error: bool = False,
    ) -> Optional[TaskRecord]:
        current = self.get_task(task_id)
        if not current:
            return None

        now = utc_now_iso()
        new_status = status if status is not None else current.status
        new_started = started_at if started_at is not None else current.started_at
        if new_status == TaskStatus.RUNNING.value and not new_started:
            new_started = now

        new_completed = completed_at if completed_at is not None else current.completed_at
        if new_status in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value) and not new_completed:
            new_completed = now

        new_error = None if clear_error else (
            redact_sensitive(error_message) if error_message is not None else current.error_message
        )
        new_hint = None if clear_error else (
            redact_sensitive(user_action_hint) if user_action_hint is not None else current.user_action_hint
        )

        new_sources = sources if sources is not None else current.sources
        new_extra = dict(current.extra)
        if extra_updates:
            new_extra.update(extra_updates)

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE tasks SET
                    status = ?,
                    updated_at = ?,
                    started_at = ?,
                    completed_at = ?,
                    external_id = ?,
                    external_url = ?,
                    worker_pid = ?,
                    detection_state = ?,
                    error_message = ?,
                    user_action_hint = ?,
                    sources_json = ?,
                    extra_json = ?
                WHERE task_id = ?
                """,
                (
                    new_status,
                    now,
                    new_started,
                    new_completed,
                    external_id if external_id is not None else current.external_id,
                    external_url if external_url is not None else current.external_url,
                    worker_pid if worker_pid is not None else current.worker_pid,
                    detection_state if detection_state is not None else current.detection_state,
                    new_error,
                    new_hint,
                    json.dumps(new_sources, ensure_ascii=False),
                    json.dumps(new_extra, ensure_ascii=False),
                    task_id,
                ),
            )
        return self.get_task(task_id)
