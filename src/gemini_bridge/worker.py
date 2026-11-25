"""Background worker and task orchestration engine for Gemini Bridge.

Supports both:
- Asynchronous background execution (spawns an independent detached OS process `python -m gemini_bridge.worker <task_id>`
  so CLI `submit` returns immediately and survives terminal closure).
- Synchronous foreground execution (`--wait` or internal worker entry point).
- Safe task resumption (`resume`) and non-destructive result re-synchronization (`fetch`).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .backends.api_backend import GeminiApiBackend
from .backends.base import BackendResult, TaskBackend
from .backends.mock_backend import MockSimulationBackend
from .backends.web_backend import WebBrowserBackend
from .config import BridgeConfig
from .storage import ResultStorage
from .tasks import DetectionState, TaskRecord, TaskStatus, TaskStore


def is_process_alive(pid: Optional[int]) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class BridgeEngine:
    """Coordinates TaskStore, ResultStorage, and execution Backends."""

    def __init__(self, config: BridgeConfig):
        self.config = config
        self.store = TaskStore(config.db_path)
        self.storage = ResultStorage(config.results_dir, config.logs_dir)

    def get_backend(self, backend_name: Optional[str] = None) -> TaskBackend:
        name = (backend_name or self.config.default_backend or "web").lower()
        if name == "web":
            return WebBrowserBackend(self.config.browser)
        elif name == "api":
            return GeminiApiBackend(self.config.api)
        elif name == "mock":
            return MockSimulationBackend()
        else:
            raise ValueError(f"Unknown backend '{name}'. Supported: 'web', 'api', 'mock'.")

    def submit_task(
        self,
        mode: str,
        prompt: str,
        prompt_file: Optional[str] = None,
        backend_name: Optional[str] = None,
        force: bool = False,
        background: bool = True,
    ) -> TaskRecord:
        chosen_backend = (backend_name or self.config.default_backend or "web").lower()
        mode_dir = self.storage.mode_subdir(mode)
        central_log = self.config.logs_dir / "bridge.log"

        task = self.store.create_task(
            mode=mode,
            prompt=prompt,
            backend=chosen_backend,
            result_dir=mode_dir,
            log_path=central_log,
            prompt_file=prompt_file,
            force=force,
        )
        self.storage.append_log(
            task,
            "CREATED",
            f"Task created (mode={mode}, backend={chosen_backend}, background={background})",
        )
        self.storage.sync_metadata(task)

        if background:
            pid = self._spawn_background_worker(task.task_id)
            task = self.store.update_task(task.task_id, worker_pid=pid) or task
            self.storage.append_log(task, "WORKER_SPAWNED", f"Detached worker started with PID {pid}")
            self.storage.sync_metadata(task)
            return task
        else:
            return self.run_worker_for_task(task.task_id, is_resume=False)

    def _spawn_background_worker(self, task_id: str) -> int:
        cmd = [
            sys.executable,
            "-m",
            "gemini_bridge.worker",
            "--config-dir",
            str(self.config.base_dir),
            "--db-path",
            str(self.config.db_path),
            "--results-dir",
            str(self.config.results_dir),
            "--logs-dir",
            str(self.config.logs_dir),
            "--task-id",
            task_id,
        ]
        env = os.environ.copy()
        src_dir = str(Path(__file__).resolve().parent.parent)
        existing_py = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing_py}" if existing_py else src_dir

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )
        return proc.pid

    def run_worker_for_task(self, task_id: str, is_resume: bool = False) -> TaskRecord:
        task = self.store.get_task(task_id)
        if not task:
            raise ValueError(f"Task '{task_id}' not found.")

        backend = self.get_backend(task.backend)
        task = self.store.update_task(
            task_id,
            status=TaskStatus.RUNNING.value,
            worker_pid=os.getpid(),
            clear_error=True,
        ) or task
        self.storage.append_log(
            task,
            "RUNNING",
            f"Worker PID {os.getpid()} starting execution (is_resume={is_resume}, backend={backend.name})",
        )
        self.storage.sync_metadata(task)

        def _on_progress(stage: str, details: Dict[str, Any]) -> None:
            ext_id = details.get("external_id")
            ext_url = details.get("external_url")
            updated = self.store.update_task(
                task_id,
                external_id=ext_id,
                external_url=ext_url,
                extra_updates={"last_stage": stage, "last_stage_details": details},
            )
            if updated:
                self.storage.append_log(updated, stage.upper(), f"Progress update: {details}")
                self.storage.sync_metadata(updated)

        try:
            if is_resume and (task.external_id or task.external_url):
                result = backend.poll_or_fetch(task, on_progress=_on_progress)
            elif is_resume and task.backend == "web" and not task.external_url:
                # If previous attempt paused at login before submitting the prompt, allow fresh submission on resume
                result = backend.execute_task(task, on_progress=_on_progress)
            else:
                result = backend.execute_task(task, on_progress=_on_progress)
        except Exception as exc:
            result = BackendResult(
                status=TaskStatus.FAILED,
                detection_state=DetectionState.UNKNOWN,
                error_message=f"Unhandled worker exception: {exc}",
            )

        return self._apply_backend_result(task_id, result)

    def fetch_task(self, task_id: str) -> TaskRecord:
        """Re-synchronize or poll an existing task without re-submitting a duplicate prompt."""
        task = self.store.get_task(task_id)
        if not task:
            raise ValueError(f"Task '{task_id}' not found.")

        # If already completed and artifacts exist on disk, re-verify and sync metadata/sources
        if task.status == TaskStatus.COMPLETED.value and task.report_path and Path(task.report_path).exists():
            report_text = Path(task.report_path).read_text(encoding="utf-8")
            sources = self.storage.finalize_task_artifacts(task, report_text, task.sources)
            updated = self.store.update_task(task_id, sources=sources) or task
            self.storage.append_log(updated, "FETCH_SYNC", "Verified and re-synced completed local artifacts.")
            self.storage.sync_metadata(updated)
            return updated

        backend = self.get_backend(task.backend)
        self.storage.append_log(task, "FETCH_POLL", f"Polling backend '{backend.name}' for task state/results.")
        result = backend.poll_or_fetch(task)
        return self._apply_backend_result(task_id, result)

    def resume_task(self, task_id: str, background: bool = False) -> TaskRecord:
        """Safely resume an interrupted, timed-out, or `needs_user_action` task."""
        task = self.store.get_task(task_id)
        if not task:
            raise ValueError(f"Task '{task_id}' not found.")

        if task.status == TaskStatus.COMPLETED.value:
            self.storage.append_log(task, "RESUME_SKIP", "Task is already completed; syncing artifacts.")
            return self.fetch_task(task_id)

        if task.status == TaskStatus.RUNNING.value and is_process_alive(task.worker_pid):
            self.storage.append_log(
                task,
                "RESUME_ALREADY_RUNNING",
                f"Worker PID {task.worker_pid} is still active; refusing to start duplicate worker.",
            )
            return task

        # Safety check: if task was previously marked running in web mode without external_url,
        # and did not explicitly stop at pre-submission login check, warn before re-submitting.
        if (
            task.status == TaskStatus.RUNNING.value
            and task.backend == "web"
            and not task.external_url
            and not is_process_alive(task.worker_pid)
        ):
            last_stage = task.extra.get("last_stage")
            if last_stage not in (None, "navigating"):
                updated = self.store.update_task(
                    task_id,
                    status=TaskStatus.NEEDS_USER_ACTION.value,
                    detection_state=DetectionState.UNKNOWN.value,
                    error_message=(
                        "Worker process terminated unexpectedly after prompt submission before thread URL was captured."
                    ),
                    user_action_hint=(
                        "Refusing to automatically re-submit to prevent duplicate tasks. "
                        "Check https://gemini.google.com/app for the running thread, or submit with `--force`."
                    ),
                ) or task
                self.storage.sync_report(updated, "")
                self.storage.sync_metadata(updated)
                return updated

        if background:
            pid = self._spawn_background_worker(task.task_id)
            task = self.store.update_task(task.task_id, status=TaskStatus.QUEUED.value, worker_pid=pid) or task
            self.storage.append_log(task, "RESUME_SPAWNED", f"Spawned background resume worker PID {pid}")
            self.storage.sync_metadata(task)
            return task
        else:
            return self.run_worker_for_task(task_id, is_resume=True)

    def wait_for_task(self, task_id: str, timeout_seconds: int = 1800, poll_interval: float = 1.0) -> TaskRecord:
        """Poll SQLite task status until it reaches a terminal or user-action state."""
        start = time.monotonic()
        while (time.monotonic() - start) < timeout_seconds:
            task = self.store.get_task(task_id)
            if not task:
                raise ValueError(f"Task '{task_id}' not found.")

            if task.status in (
                TaskStatus.COMPLETED.value,
                TaskStatus.FAILED.value,
                TaskStatus.NEEDS_USER_ACTION.value,
                TaskStatus.CANCELLED.value,
            ):
                return task

            # Detect orphaned worker process
            if task.status in (TaskStatus.QUEUED.value, TaskStatus.RUNNING.value) and task.worker_pid:
                if not is_process_alive(task.worker_pid):
                    # Give SQLite a brief moment in case worker just finished writing
                    time.sleep(0.3)
                    refreshed = self.store.get_task(task_id)
                    if refreshed and refreshed.status in (TaskStatus.QUEUED.value, TaskStatus.RUNNING.value):
                        updated = self.store.update_task(
                            task_id,
                            status=TaskStatus.NEEDS_USER_ACTION.value,
                            detection_state=DetectionState.UNKNOWN.value,
                            error_message=f"Background worker process (PID {task.worker_pid}) exited unexpectedly.",
                            user_action_hint=f"Run `gemini-bridge resume {task_id}` to inspect and recover.",
                        )
                        if updated:
                            self.storage.sync_metadata(updated)
                            return updated

            time.sleep(poll_interval)

        task = self.store.get_task(task_id)
        if not task:
            raise ValueError(f"Task '{task_id}' not found.")
        return task

    def _apply_backend_result(self, task_id: str, result: BackendResult) -> TaskRecord:
        task = self.store.get_task(task_id)
        if not task:
            raise ValueError(f"Task '{task_id}' not found.")

        updated = self.store.update_task(
            task_id,
            status=result.status.value,
            detection_state=result.detection_state.value,
            external_id=result.external_id,
            external_url=result.external_url,
            error_message=result.error_message,
            user_action_hint=result.user_action_hint,
            extra_updates=result.extra,
        ) or task

        self.storage.append_log(
            updated,
            f"RESULT_{result.status.value.upper()}",
            result.error_message or f"Completed with {len(result.report_markdown)} chars",
            level="INFO" if result.status == TaskStatus.COMPLETED else "WARN",
        )
        sources = self.storage.finalize_task_artifacts(updated, result.report_markdown, result.sources)
        updated = self.store.update_task(task_id, sources=sources) or updated
        self.storage.sync_metadata(updated)
        return updated


def main_worker() -> None:
    """Entry point for detached background worker subprocess."""
    import argparse

    parser = argparse.ArgumentParser(description="Gemini Bridge background task worker")
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--logs-dir", required=True)
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args()

    cfg = BridgeConfig.load(base_dir=Path(args.config_dir))
    cfg.db_path = Path(args.db_path).resolve()
    cfg.results_dir = Path(args.results_dir).resolve()
    cfg.logs_dir = Path(args.logs_dir).resolve()
    cfg.resolve_paths()

    engine = BridgeEngine(cfg)
    engine.run_worker_for_task(args.task_id, is_resume=False)


if __name__ == "__main__":
    main_worker()
