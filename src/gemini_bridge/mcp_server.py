"""Model Context Protocol (MCP) Server for Gemini Bridge.

Implements a full JSON-RPC 2.0 STDIO MCP Server (zero external SDK dependency required,
compatible with Claude Code, Codex, Cursor, and MCP Inspector) exposing:
- submit_task
- get_task_status
- wait_for_task
- get_task_result
- list_tasks
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import BridgeConfig
from .tasks import DuplicateTaskError, TaskRecord
from .worker import BridgeEngine

MCP_TOOLS_SCHEMA: List[Dict[str, Any]] = [
    {
        "name": "submit_task",
        "description": (
            "Submit a research or workflow prompt to Gemini Bridge ('deep-research' or 'app-task'). "
            "Returns structured task metadata including task_id, status, and local result file paths."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["deep-research", "app-task"],
                    "default": "deep-research",
                    "description": "Task mode: 'deep-research' or 'app-task'.",
                },
                "prompt": {
                    "type": "string",
                    "description": "Prompt text to submit (provide either prompt or file_path).",
                },
                "file_path": {
                    "type": "string",
                    "description": "Optional local Markdown/text file path containing the prompt.",
                },
                "backend": {
                    "type": "string",
                    "enum": ["web", "api", "mock"],
                    "description": "Execution backend ('web' reuses browser session; 'api' uses Interactions API).",
                },
                "background": {
                    "type": "boolean",
                    "default": True,
                    "description": "If true, runs in background and returns task_id immediately.",
                },
                "force": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, bypass duplicate active task protection.",
                },
            },
        },
    },
    {
        "name": "get_task_status",
        "description": "Query the current status, detection state, and artifact paths of a Gemini Bridge task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Unique task ID (e.g. dr-20261025-...).",
                }
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "wait_for_task",
        "description": "Wait for a running or queued Gemini Bridge task to reach a terminal or user-action state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Unique task ID.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "default": 300,
                    "description": "Maximum seconds to wait before returning current state.",
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "get_task_result",
        "description": (
            "Fetch and return the synchronized Markdown report, verified sources, and file paths for a task."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Unique task ID.",
                },
                "resync": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, actively polls backend to re-synchronize before reading files.",
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "list_tasks",
        "description": "List recent Gemini Bridge tasks with optional filtering by mode or status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["deep-research", "app-task"],
                },
                "status": {
                    "type": "string",
                    "enum": ["queued", "running", "completed", "failed", "needs_user_action", "cancelled"],
                },
                "limit": {
                    "type": "integer",
                    "default": 20,
                },
            },
        },
    },
]


def format_task_payload(task: TaskRecord, include_report: bool = False) -> Dict[str, Any]:
    payload = {
        "task_id": task.task_id,
        "mode": task.mode,
        "status": task.status,
        "detection_state": task.detection_state,
        "backend": task.backend,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "completed_at": task.completed_at,
        "external_id": task.external_id,
        "external_url": task.external_url,
        "result_paths": {
            "result_dir": task.result_dir,
            "report_path": task.report_path,
            "metadata_path": task.metadata_path,
            "sources_path": task.sources_path,
            "log_path": task.log_path,
        },
        "sources_count": len(task.sources),
        "sources": task.sources,
        "error_message": task.error_message,
        "user_action_hint": task.user_action_hint,
    }
    if include_report:
        report_content = ""
        if task.report_path and Path(task.report_path).exists():
            report_content = Path(task.report_path).read_text(encoding="utf-8")
        payload["report_markdown"] = report_content
    return payload


class GeminiBridgeMcpServer:
    """JSON-RPC 2.0 MCP Server handler."""

    def __init__(self, config: BridgeConfig):
        self.config = config
        self.engine = BridgeEngine(config)

    def handle_tool_call(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if name == "submit_task":
                mode = arguments.get("mode", "deep-research")
                prompt = arguments.get("prompt")
                file_path = arguments.get("file_path")
                if file_path and not prompt:
                    p = Path(file_path).expanduser().resolve()
                    if not p.exists():
                        return {"ok": False, "error": f"Prompt file not found: {file_path}"}
                    prompt = p.read_text(encoding="utf-8")
                if not prompt:
                    return {"ok": False, "error": "Either 'prompt' or 'file_path' must be provided."}

                task = self.engine.submit_task(
                    mode=mode,
                    prompt=prompt,
                    prompt_file=file_path,
                    backend_name=arguments.get("backend"),
                    force=bool(arguments.get("force", False)),
                    background=bool(arguments.get("background", True)),
                )
                return {"ok": True, "task": format_task_payload(task)}

            elif name == "get_task_status":
                task_id = arguments["task_id"]
                task = self.engine.store.get_task(task_id)
                if not task:
                    return {"ok": False, "error": f"Task '{task_id}' not found."}
                return {"ok": True, "task": format_task_payload(task)}

            elif name == "wait_for_task":
                task_id = arguments["task_id"]
                timeout_sec = int(arguments.get("timeout_seconds", 300))
                task = self.engine.wait_for_task(task_id, timeout_seconds=timeout_sec)
                return {"ok": True, "task": format_task_payload(task)}

            elif name == "get_task_result":
                task_id = arguments["task_id"]
                if arguments.get("resync"):
                    task = self.engine.fetch_task(task_id)
                else:
                    task = self.engine.store.get_task(task_id)
                if not task:
                    return {"ok": False, "error": f"Task '{task_id}' not found."}
                return {"ok": True, "task": format_task_payload(task, include_report=True)}

            elif name == "list_tasks":
                tasks = self.engine.store.list_tasks(
                    mode=arguments.get("mode"),
                    status=arguments.get("status"),
                    limit=int(arguments.get("limit", 20)),
                )
                return {
                    "ok": True,
                    "count": len(tasks),
                    "tasks": [format_task_payload(t) for t in tasks],
                }

            else:
                return {"ok": False, "error": f"Unknown tool: {name}"}

        except DuplicateTaskError as dup_err:
            return {
                "ok": False,
                "error": str(dup_err),
                "existing_task_id": dup_err.existing_task_id,
                "existing_status": dup_err.status,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def handle_jsonrpc_message(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        # Notifications have no id and expect no response
        if msg_id is None:
            return None

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "gemini-bridge", "version": "0.1.0"},
                },
            }

        elif method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": MCP_TOOLS_SCHEMA},
            }

        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments") or {}
            outcome = self.handle_tool_call(tool_name, tool_args)
            is_err = not outcome.get("ok", False)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(outcome, ensure_ascii=False, indent=2),
                        }
                    ],
                    "isError": is_err,
                },
            }

        else:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

    def serve_stdio(self) -> None:
        """Run the STDIO JSON-RPC loop."""
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception as exc:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                }
                sys.stdout.write(json.dumps(err_resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
                continue

            resp = self.handle_jsonrpc_message(msg)
            if resp is not None:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
