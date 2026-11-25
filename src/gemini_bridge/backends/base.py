"""Base backend interface and normalized execution result structures."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..tasks import DetectionState, TaskRecord, TaskStatus


@dataclass
class BackendResult:
    status: TaskStatus
    detection_state: DetectionState = DetectionState.VERIFIED
    report_markdown: str = ""
    sources: List[Dict[str, Any]] = field(default_factory=list)
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    error_message: Optional[str] = None
    user_action_hint: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class TaskBackend(ABC):
    """Abstract interface implemented by WebBrowserBackend, GeminiApiBackend, and MockBackend."""

    name: str = "base"

    @abstractmethod
    def check_health(self) -> Dict[str, Any]:
        """Verify environment, credentials/login session, and readiness for `gemini-bridge doctor`."""

    @abstractmethod
    def execute_task(
        self,
        task: TaskRecord,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> BackendResult:
        """Submit and track a task until completion, failure, or user intervention required."""

    @abstractmethod
    def poll_or_fetch(
        self,
        task: TaskRecord,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> BackendResult:
        """Fetch or re-synchronize an existing task by its external_id or external_url without re-submitting."""
