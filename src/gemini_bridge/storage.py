"""Local result synchronization and artifact generation for Gemini Bridge.

Every task automatically maintains and synchronizes four UTF-8 files in its result directory:
1. report.md: Full research report or task output, preserving original citations and inline links.
2. metadata.json: Structured task metadata, timestamps, status, detection state, and file paths.
3. sources.md: Extracted and verified source URLs and titles (never fabricating unverified citations).
4. task.log: Sanitized chronological log of submission, state transitions, errors, and recovery steps.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import redact_sensitive
from .tasks import SourceItem, TaskRecord, utc_now_iso

# Regex for extracting standard Markdown links: [Title](https://...)
_MD_LINK_RE = re.compile(r"\[([^\[\]]+)\]\((https?://[^\s\)]+)\)")
# Regex for bare URLs not inside Markdown parentheses
_BARE_URL_RE = re.compile(r"(?<!\()(https?://[^\s\)\]>\"']+)")


def extract_sources_from_markdown(markdown_text: str, explicit_sources: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Extract and deduplicate sources from report text and explicit DOM/API citation lists.

    Strictly preserves authentic URLs and titles from the source report without fabricating citations.
    """
    seen_urls: Dict[str, Dict[str, Any]] = {}
    ordered: List[Dict[str, Any]] = []

    def _add(title: str, url: str, snippet: Optional[str] = None, citation_index: Optional[int] = None) -> None:
        clean_url = url.strip().rstrip(".,;:")
        if not clean_url.startswith(("http://", "https://")):
            return
        clean_title = (title or "").strip() or clean_url
        if clean_url in seen_urls:
            existing = seen_urls[clean_url]
            if existing["title"] == clean_url and clean_title != clean_url:
                existing["title"] = clean_title
            if snippet and not existing.get("snippet"):
                existing["snippet"] = snippet
            return
        item = SourceItem(
            title=clean_title,
            url=clean_url,
            snippet=snippet,
            citation_index=citation_index if citation_index is not None else len(ordered) + 1,
        ).to_dict()
        seen_urls[clean_url] = item
        ordered.append(item)

    if explicit_sources:
        for idx, src in enumerate(explicit_sources, start=1):
            if isinstance(src, dict) and src.get("url"):
                _add(
                    title=src.get("title") or src["url"],
                    url=src["url"],
                    snippet=src.get("snippet"),
                    citation_index=src.get("citation_index") or idx,
                )

    if markdown_text:
        for match in _MD_LINK_RE.finditer(markdown_text):
            title, url = match.group(1), match.group(2)
            _add(title=title, url=url)

        for match in _BARE_URL_RE.finditer(markdown_text):
            url = match.group(1)
            _add(title=url, url=url)

    return ordered


class ResultStorage:
    """Manages local filesystem synchronization for task artifacts."""

    def __init__(self, results_dir: Path, logs_dir: Path):
        self.results_dir = Path(results_dir).resolve()
        self.logs_dir = Path(logs_dir).resolve()
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def mode_subdir(self, mode: str) -> Path:
        if mode == "deep-research":
            sub = self.results_dir / "deep-research"
        else:
            sub = self.results_dir / "app-tasks"
        sub.mkdir(parents=True, exist_ok=True)
        return sub

    def append_log(self, task: TaskRecord, event: str, message: str, level: str = "INFO") -> None:
        """Append a sanitized log line to both the task's task.log and the central logs directory."""
        ts = utc_now_iso()
        safe_msg = redact_sensitive(message)
        line = f"[{ts}] [{level.upper()}] [{task.task_id}] [{event}] {safe_msg}\n"

        if task.log_path:
            task_log = Path(task.log_path)
            task_log.parent.mkdir(parents=True, exist_ok=True)
            with open(task_log, "a", encoding="utf-8") as f:
                f.write(line)

        central_log = self.logs_dir / f"{task.task_id}.log"
        with open(central_log, "a", encoding="utf-8") as f:
            f.write(line)

    def sync_metadata(self, task: TaskRecord) -> Path:
        """Write or update metadata.json for the given task."""
        if not task.metadata_path:
            raise ValueError(f"Task {task.task_id} has no metadata_path configured.")
        meta_path = Path(task.metadata_path)
        meta_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "task_id": task.task_id,
            "mode": task.mode,
            "status": task.status,
            "detection_state": task.detection_state,
            "backend": task.backend,
            "prompt": task.prompt,
            "prompt_file": task.prompt_file,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
            "external_id": task.external_id,
            "external_url": task.external_url,
            "worker_pid": task.worker_pid,
            "error_message": task.error_message,
            "user_action_hint": task.user_action_hint,
            "files": {
                "result_dir": task.result_dir,
                "report_path": task.report_path,
                "metadata_path": task.metadata_path,
                "sources_path": task.sources_path,
                "log_path": task.log_path,
            },
            "sources_count": len(task.sources),
            "sources": task.sources,
            "extra": task.extra,
        }

        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return meta_path

    def sync_sources(self, task: TaskRecord, sources: List[Dict[str, Any]]) -> Path:
        """Write sources.md containing all verified URLs and titles."""
        if not task.sources_path:
            raise ValueError(f"Task {task.task_id} has no sources_path configured.")
        src_path = Path(task.sources_path)
        src_path.parent.mkdir(parents=True, exist_ok=True)

        lines = [
            f"# Sources for Task `{task.task_id}`",
            "",
            f"- **Mode**: `{task.mode}`",
            f"- **Status**: `{task.status}`",
            f"- **Synced At**: `{utc_now_iso()}`",
            f"- **Total Verified Sources**: {len(sources)}",
        ]
        if task.external_url:
            lines.append(f"- **Origin Thread / URL**: {task.external_url}")
        lines.append("")

        if not sources:
            lines.extend([
                "_No verified external source URLs were extracted for this task._",
                "",
                "> Note: Gemini Bridge strictly preserves authentic citations from the original output and never fabricates unverified URLs.",
                "",
            ])
        else:
            lines.append("## Verified Source Links")
            lines.append("")
            for idx, item in enumerate(sources, start=1):
                title = item.get("title") or item.get("url")
                url = item.get("url", "")
                snippet = item.get("snippet")
                lines.append(f"{idx}. [{title}]({url})")
                lines.append(f"   - URL: `{url}`")
                if snippet:
                    clean_snip = " ".join(str(snippet).splitlines()).strip()
                    lines.append(f"   - Context: {clean_snip}")
            lines.append("")

        with open(src_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return src_path

    def sync_report(self, task: TaskRecord, report_markdown: str) -> Path:
        """Write report.md preserving full report text, citations, and header metadata."""
        if not task.report_path:
            raise ValueError(f"Task {task.task_id} has no report_path configured.")
        rep_path = Path(task.report_path)
        rep_path.parent.mkdir(parents=True, exist_ok=True)

        body = (report_markdown or "").strip()
        if not body:
            body = (
                f"# Task `{task.task_id}` ({task.mode})\n\n"
                f"**Status**: `{task.status}`\n\n"
                f"**Error / Status Note**: {task.error_message or task.user_action_hint or 'No report body captured yet.'}\n"
            )

        with open(rep_path, "w", encoding="utf-8") as f:
            f.write(body + "\n")
        return rep_path

    def finalize_task_artifacts(
        self,
        task: TaskRecord,
        report_markdown: str,
        explicit_sources: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Synchronize report.md, sources.md, and metadata.json atomically."""
        extracted_sources = extract_sources_from_markdown(report_markdown, explicit_sources)
        task.sources = extracted_sources
        self.sync_report(task, report_markdown)
        self.sync_sources(task, extracted_sources)
        self.sync_metadata(task)
        return extracted_sources
