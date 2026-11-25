"""24/7 Autonomy Daemon & Watchdog Process Supervisor for AutoPi.

Runs the self-driving heartbeat loop, automatically catches unexpected errors,
recovers uncompleted tasks from SQLite state, and resumes without human prompting.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional

from .engine import AutonomyEngine

logger = logging.getLogger("autopi.daemon")


class AutonomyDaemon:
    def __init__(
        self,
        engine: AutonomyEngine,
        heartbeat_interval_sec: float = 3.0,
        max_consecutive_crashes: int = 5,
    ) -> None:
        self.engine = engine
        self.heartbeat_interval_sec = heartbeat_interval_sec
        self.max_consecutive_crashes = max_consecutive_crashes

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._consecutive_crashes = 0
        self._iteration_count = 0
        self._last_error = ""

    @property
    def is_running(self) -> bool:
        return self._running and (self._thread is not None and self._thread.is_alive())

    def start(self) -> None:
        if self.is_running:
            logger.warning("Daemon is already running.")
            return

        self._running = True
        self.engine.db.set_loop_state("status", "running")
        self._thread = threading.Thread(target=self._daemon_loop, name="AutoPi-Heartbeat", daemon=True)
        self._thread.start()
        logger.info("AutoPi 24/7 Daemon started (heartbeat: %.1fs).", self.heartbeat_interval_sec)

    def pause(self) -> None:
        self.engine.db.set_loop_state("status", "paused")
        logger.info("AutoPi Daemon paused by supervisor.")

    def resume(self) -> None:
        self.engine.db.set_loop_state("status", "running")
        self._consecutive_crashes = 0
        if not self.is_running:
            self._running = True
            self._thread = threading.Thread(target=self._daemon_loop, name="AutoPi-Heartbeat", daemon=True)
            self._thread.start()
        logger.info("AutoPi Daemon resumed by supervisor.")

    def emergency_stop(self) -> None:
        self.engine.db.set_loop_state("status", "emergency_stopped")
        self.engine.executor.emergency_kill()
        self._running = False
        logger.warning("AutoPi Emergency Stop triggered! Worker process killed.")

    def stop(self) -> None:
        self._running = False
        self.engine.db.set_loop_state("status", "stopped")
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        logger.info("AutoPi Daemon stopped cleanly.")

    def _daemon_loop(self) -> None:
        while self._running:
            try:
                res = self.engine.run_next_iteration()
                self._iteration_count += 1
                self._consecutive_crashes = 0

                # Log step progress
                status = res.get("status")
                if status == "iterated":
                    logger.info(
                        "Iteration #%d: Task '%s' -> Verdict: %s (Progress: %d%%)",
                        self._iteration_count,
                        res.get("title"),
                        res.get("verdict"),
                        res.get("progress_pct", 0),
                    )
                elif status == "phase_advanced":
                    logger.info("Milestone Phase Advanced: %s", res.get("message"))
                elif status in ("paused", "emergency_stopped"):
                    time.sleep(self.heartbeat_interval_sec)
                    continue

                # Sleep before next self-driving step
                time.sleep(self.heartbeat_interval_sec)

            except Exception as exc:
                self._consecutive_crashes += 1
                self._last_error = str(exc)
                logger.error(
                    "WATCHDOG: Caught unhandled error in autonomous loop (crash count: %d/%d): %s",
                    self._consecutive_crashes,
                    self.max_consecutive_crashes,
                    exc,
                    exc_info=True,
                )

                if self._consecutive_crashes >= self.max_consecutive_crashes:
                    logger.critical("WATCHDOG: Too many consecutive crashes. Pausing loop for human inspection.")
                    self.pause()
                    break

                # Self-healing recovery backoff
                time.sleep(2.0)

    def get_status_telemetry(self) -> Dict[str, Any]:
        active_goal = self.engine.db.get_active_goal()
        sub_tasks = self.engine.db.list_sub_tasks(active_goal["id"]) if active_goal else []
        recent_iters = self.engine.db.list_recent_iterations(limit=5)
        artifacts = self.engine.db.list_artifacts()
        loop_status = self.engine.db.get_loop_state("status", "running")

        return {
            "is_running": self.is_running,
            "loop_status": loop_status,
            "iteration_count": self._iteration_count,
            "consecutive_crashes": self._consecutive_crashes,
            "last_error": self._last_error,
            "active_goal": active_goal,
            "sub_tasks_count": len(sub_tasks),
            "completed_sub_tasks": sum(1 for t in sub_tasks if t["status"] == "completed"),
            "recent_iterations": recent_iters,
            "artifacts_count": len(artifacts),
        }
