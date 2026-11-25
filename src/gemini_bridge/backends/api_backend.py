"""Official Gemini Interactions API Backend (Priority 2: Optional fallback when GEMINI_API_KEY is configured).

Uses the official Gemini Interactions API (`POST /v1beta/interactions` with `background=true`
and `GET /v1beta/interactions/{id}`) for `deep-research-preview-04-2026` or `deep-research-max-preview-04-2026`.

Important Boundaries:
1. Never enabled unless the user explicitly provides `GEMINI_API_KEY` and sets `--backend api` or `api.enabled: true`.
2. Gemini Web subscriptions (Google AI Pro / Ultra) do NOT include Gemini API quota; API calls are billed pay-as-you-go.
3. Gemini App Tasks (Web Scheduled Actions / Consumer Spark Tasks) have NO public API. If `--mode app-task` is called
   with `--backend api`, it returns a clear `needs_user_action` / unsupported boundary explanation unless the user
   explicitly opts into `allow_api_fallback_for_app_task` (which uses the managed `antigravity-preview-09-2026` agent).
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

import httpx

from ..config import ApiConfig, redact_sensitive
from ..storage import extract_sources_from_markdown
from ..tasks import DetectionState, TaskMode, TaskRecord, TaskStatus
from .base import BackendResult, TaskBackend


class GeminiApiBackend(TaskBackend):
    """Official Gemini Interactions API backend."""

    name = "api"

    def __init__(self, config: ApiConfig):
        self.config = config

    def check_health(self) -> Dict[str, Any]:
        api_key = self.config.get_api_key()
        has_key = bool(api_key and len(api_key.strip()) > 10)
        return {
            "backend": self.name,
            "enabled": self.config.enabled or has_key,
            "api_key_configured": has_key,
            "api_key_env": self.config.api_key_env,
            "base_url": self.config.base_url,
            "deep_research_agent": self.config.deep_research_agent,
            "app_task_public_api_available": False,
            "app_task_fallback_agent": self.config.app_task_fallback_agent,
            "allow_api_fallback_for_app_task": self.config.allow_api_fallback_for_app_task,
            "billing_notice": (
                "Official Gemini API uses pay-as-you-go billing in Google AI Studio (~$1-$3 per Deep Research task) "
                "and is separate from consumer Google AI Pro/Ultra web subscriptions."
            ),
        }

    def execute_task(
        self,
        task: TaskRecord,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> BackendResult:
        api_key = self.config.get_api_key()
        if not api_key:
            return BackendResult(
                status=TaskStatus.NEEDS_USER_ACTION,
                detection_state=DetectionState.VERIFIED,
                error_message=(
                    f"Official Gemini API backend requires `{self.config.api_key_env}` environment variable, "
                    "which is not set."
                ),
                user_action_hint=(
                    "Either use `--backend web` (default, reuses your Gemini web account) "
                    f"or export `{self.config.api_key_env}=your_api_key` to use the paid Gemini Interactions API."
                ),
            )

        if task.mode == TaskMode.APP_TASK.value and not self.config.allow_api_fallback_for_app_task:
            return BackendResult(
                status=TaskStatus.NEEDS_USER_ACTION,
                detection_state=DetectionState.VERIFIED,
                error_message=(
                    "Google does NOT provide a public REST/SDK API for consumer Gemini App Tasks or Scheduled Actions."
                ),
                user_action_hint=(
                    "Fact Verification Summary:\n"
                    "1. Consumer 'Tasks in Gemini Apps' (https://support.google.com/gemini/answer/17094507) and "
                    "'Scheduled Actions' (https://support.google.com/gemini/answer/16316416) are strictly web/mobile UI features.\n"
                    "2. They are distinct from the developer 'Antigravity Agent' (`antigravity-preview-09-2026`) on the Gemini Interactions API.\n"
                    "To execute via web UI, use `--backend web`. If you want to substitute the developer Antigravity Agent via API, "
                    "set `api.allow_api_fallback_for_app_task: true` in config.yaml."
                ),
            )

        agent_name = (
            self.config.deep_research_agent
            if task.mode == TaskMode.DEEP_RESEARCH.value
            else self.config.app_task_fallback_agent
        )

        create_url = f"{self.config.base_url.rstrip('/')}/interactions"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }
        payload: Dict[str, Any] = {
            "agent": agent_name,
            "input": task.prompt,
            "background": True,
        }
        if task.mode == TaskMode.DEEP_RESEARCH.value:
            payload["agent_config"] = {
                "type": "deep-research",
                "thinking_summaries": "auto",
                "collaborative_planning": False,
            }
        else:
            payload["environment"] = "remote"

        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(create_url, headers=headers, json=payload)
                if resp.status_code >= 400:
                    return BackendResult(
                        status=TaskStatus.FAILED,
                        detection_state=DetectionState.VERIFIED,
                        error_message=redact_sensitive(
                            f"Interactions API HTTP {resp.status_code}: {resp.text[:500]}"
                        ),
                    )
                data = resp.json()
        except Exception as exc:
            return BackendResult(
                status=TaskStatus.FAILED,
                detection_state=DetectionState.UNKNOWN,
                error_message=redact_sensitive(f"Failed to submit interaction to Gemini API: {exc}"),
            )

        interaction_id = data.get("id") or data.get("interaction", {}).get("id")
        if not interaction_id:
            return BackendResult(
                status=TaskStatus.FAILED,
                detection_state=DetectionState.DOM_CHANGED_OR_UNPARSEABLE,
                error_message=f"Gemini Interactions API did not return an interaction ID: {data}",
            )

        if on_progress:
            on_progress("submitted", {"external_id": interaction_id, "agent": agent_name})

        task.external_id = interaction_id
        task.external_url = f"{self.config.base_url.rstrip('/')}/interactions/{interaction_id}"
        return self.poll_or_fetch(task, on_progress=on_progress)

    def poll_or_fetch(
        self,
        task: TaskRecord,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> BackendResult:
        api_key = self.config.get_api_key()
        if not api_key:
            return BackendResult(
                status=TaskStatus.NEEDS_USER_ACTION,
                detection_state=DetectionState.UNKNOWN,
                error_message=f"Missing `{self.config.api_key_env}` to poll interaction `{task.external_id}`.",
            )
        if not task.external_id:
            return BackendResult(
                status=TaskStatus.FAILED,
                detection_state=DetectionState.UNKNOWN,
                error_message="Task has no `external_id` (interaction ID) recorded; cannot poll API.",
            )

        poll_url = f"{self.config.base_url.rstrip('/')}/interactions/{task.external_id}"
        headers = {"x-goog-api-key": api_key}
        start_ts = time.monotonic()
        timeout_sec = max(30, self.config.task_timeout_seconds)
        poll_sec = max(2, self.config.poll_interval_seconds)

        while (time.monotonic() - start_ts) < timeout_sec:
            try:
                with httpx.Client(timeout=30.0) as client:
                    resp = client.get(poll_url, headers=headers)
                    if resp.status_code >= 400:
                        return BackendResult(
                            status=TaskStatus.FAILED,
                            detection_state=DetectionState.VERIFIED,
                            external_id=task.external_id,
                            external_url=poll_url,
                            error_message=redact_sensitive(
                                f"Interactions API poll HTTP {resp.status_code}: {resp.text[:500]}"
                            ),
                        )
                    body = resp.json()
            except Exception as exc:
                return BackendResult(
                    status=TaskStatus.FAILED,
                    detection_state=DetectionState.UNKNOWN,
                    external_id=task.external_id,
                    external_url=poll_url,
                    error_message=redact_sensitive(f"Network error polling Interactions API: {exc}"),
                )

            interaction = body.get("interaction", body)
            status_str = str(interaction.get("status", "")).lower()

            if status_str == "completed":
                report_md, api_sources = self._extract_interaction_output(interaction)
                return BackendResult(
                    status=TaskStatus.COMPLETED,
                    detection_state=DetectionState.VERIFIED,
                    report_markdown=report_md,
                    sources=api_sources,
                    external_id=task.external_id,
                    external_url=poll_url,
                )
            elif status_str in ("failed", "error", "cancelled"):
                err_detail = interaction.get("error") or interaction.get("errors") or "Interaction failed"
                return BackendResult(
                    status=TaskStatus.FAILED,
                    detection_state=DetectionState.VERIFIED,
                    external_id=task.external_id,
                    external_url=poll_url,
                    error_message=redact_sensitive(str(err_detail)),
                )
            elif status_str == "requires_action":
                return BackendResult(
                    status=TaskStatus.NEEDS_USER_ACTION,
                    detection_state=DetectionState.VERIFIED,
                    external_id=task.external_id,
                    external_url=poll_url,
                    error_message="Interaction returned status 'requires_action'.",
                    user_action_hint="Review pending function calls or plan approval for this interaction.",
                )

            if on_progress:
                on_progress("polling", {"external_id": task.external_id, "api_status": status_str or "in_progress"})
            time.sleep(poll_sec)

        return BackendResult(
            status=TaskStatus.NEEDS_USER_ACTION,
            detection_state=DetectionState.UNKNOWN,
            external_id=task.external_id,
            external_url=poll_url,
            error_message=f"Timed out after {timeout_sec}s polling Interactions API.",
            user_action_hint=f"Run `gemini-bridge fetch {task.task_id}` to check if the background interaction has finished.",
        )

    @staticmethod
    def _extract_interaction_output(interaction: Dict[str, Any]) -> tuple[str, List[Dict[str, Any]]]:
        """Extract final markdown text and grounded citations from an Interaction response object."""
        text_chunks: List[str] = []
        explicit_sources: List[Dict[str, Any]] = []

        if interaction.get("output_text"):
            text_chunks.append(str(interaction["output_text"]))
        elif interaction.get("outputText"):
            text_chunks.append(str(interaction["outputText"]))

        steps = interaction.get("steps") or []
        for step in steps:
            if not isinstance(step, dict):
                continue
            contents = step.get("content") or []
            for item in contents:
                if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                    text_chunks.append(str(item["text"]))
                if isinstance(item, dict) and item.get("citations"):
                    for cit in item["citations"]:
                        if isinstance(cit, dict) and cit.get("url"):
                            explicit_sources.append({
                                "title": cit.get("title") or cit["url"],
                                "url": cit["url"],
                                "snippet": cit.get("snippet"),
                            })

        final_md = "\n\n".join(text_chunks).strip()
        sources = extract_sources_from_markdown(final_md, explicit_sources)
        return final_md, sources
