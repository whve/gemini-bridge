"""Command Line Interface (CLI) for Gemini Bridge.

Provides human-readable and JSON-structured (`--json`) commands for local AI Agents
(Claude Code, Codex, etc.):
- `gemini-bridge submit`
- `gemini-bridge status <task_id>`
- `gemini-bridge wait <task_id>`
- `gemini-bridge fetch <task_id>`
- `gemini-bridge list`
- `gemini-bridge resume <task_id>`
- `gemini-bridge doctor`
- `gemini-bridge mcp`
- `gemini-bridge serve`
"""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import click

from .config import BridgeConfig
from .http_server import run_local_http_server
from .mcp_server import GeminiBridgeMcpServer, format_task_payload
from .tasks import DuplicateTaskError, TaskRecord, TaskStatus
from .worker import BridgeEngine


def _emit(data: Dict[str, Any], as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if not data.get("ok", True):
        click.secho(f"Error: {data.get('error')}", fg="red", err=True)
        if data.get("existing_task_id"):
            click.echo(f"Existing Task ID: {data['existing_task_id']} (status: {data.get('existing_status')})")
        return

    if "task" in data:
        t = data["task"]
        status_color = {
            "completed": "green",
            "running": "cyan",
            "queued": "yellow",
            "needs_user_action": "magenta",
            "failed": "red",
            "cancelled": "red",
        }.get(t["status"], "white")
        click.echo(f"Task ID        : {t['task_id']}")
        click.echo(f"Mode           : {t['mode']}")
        click.echo(f"Backend        : {t['backend']}")
        click.echo(f"Status         : {click.style(t['status'], fg=status_color, bold=True)} (detection: {t['detection_state']})")
        click.echo(f"Created At     : {t['created_at']}")
        if t.get("completed_at"):
            click.echo(f"Completed At   : {t['completed_at']}")
        if t.get("external_url"):
            click.echo(f"External URL   : {t['external_url']}")
        paths = t.get("result_paths", {})
        click.echo(f"Report File    : {paths.get('report_path')}")
        click.echo(f"Metadata File  : {paths.get('metadata_path')}")
        click.echo(f"Sources File   : {paths.get('sources_path')} ({t.get('sources_count', 0)} sources)")
        click.echo(f"Task Log       : {paths.get('log_path')}")
        if t.get("error_message"):
            click.secho(f"Error / Notice : {t['error_message']}", fg="yellow")
        if t.get("user_action_hint"):
            click.secho(f"Action Needed  :\n{t['user_action_hint']}", fg="magenta")


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--config", "config_path", type=click.Path(), default=None, help="Path to config.yaml file.")
@click.option("--home", "base_dir", type=click.Path(), default=None, help="Project working directory.")
@click.version_option("0.1.0", prog_name="gemini-bridge")
@click.pass_context
def cli(ctx: click.Context, config_path: Optional[str], base_dir: Optional[str]) -> None:
    """Gemini Bridge: Local AI Task Bridge for Gemini Deep Research and Gemini App Tasks."""
    ctx.ensure_object(dict)
    cfg = BridgeConfig.load(
        config_path=config_path,
        base_dir=Path(base_dir) if base_dir else None,
    )
    ctx.obj["config"] = cfg
    ctx.obj["engine"] = BridgeEngine(cfg)


@cli.command("submit")
@click.option(
    "--mode",
    type=click.Choice(["deep-research", "app-task"], case_sensitive=False),
    default="deep-research",
    show_default=True,
    help="Task type: 'deep-research' or 'app-task'.",
)
@click.option("--file", "prompt_file", type=click.Path(exists=True), default=None, help="Path to Markdown/text prompt file.")
@click.option("--prompt", "prompt_text", type=str, default=None, help="Inline prompt string.")
@click.option(
    "--backend",
    type=click.Choice(["web", "api", "mock"], case_sensitive=False),
    default=None,
    help="Execution backend (defaults to config default_backend, which is 'web').",
)
@click.option("--wait/--no-wait", default=False, show_default=True, help="Wait synchronously for task completion.")
@click.option("--force", is_flag=True, default=False, help="Allow submission even if an identical active prompt exists.")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output structured JSON for Agent consumption.")
@click.pass_context
def cmd_submit(
    ctx: click.Context,
    mode: str,
    prompt_file: Optional[str],
    prompt_text: Optional[str],
    backend: Optional[str],
    wait: bool,
    force: bool,
    as_json: bool,
) -> None:
    """Submit a new Gemini Deep Research or Gemini App Task."""
    engine: BridgeEngine = ctx.obj["engine"]

    if prompt_file and prompt_text:
        _emit({"ok": False, "error": "Specify either --file or --prompt, not both."}, as_json)
        sys.exit(2)

    resolved_file: Optional[str] = None
    if prompt_file:
        p = Path(prompt_file).expanduser().resolve()
        resolved_file = str(p)
        content = p.read_text(encoding="utf-8")
    elif prompt_text is not None:
        content = prompt_text
    else:
        _emit({"ok": False, "error": "Must provide either --file <path> or --prompt <text>."}, as_json)
        sys.exit(2)

    if not content.strip():
        _emit({"ok": False, "error": "Prompt content cannot be empty."}, as_json)
        sys.exit(2)

    try:
        task = engine.submit_task(
            mode=mode.lower(),
            prompt=content,
            prompt_file=resolved_file,
            backend_name=backend,
            force=force,
            background=not wait,
        )
        _emit({"ok": True, "task": format_task_payload(task)}, as_json)
    except DuplicateTaskError as dup:
        _emit(
            {
                "ok": False,
                "error": str(dup),
                "existing_task_id": dup.existing_task_id,
                "existing_status": dup.status,
            },
            as_json,
        )
        sys.exit(1)
    except Exception as exc:
        _emit({"ok": False, "error": str(exc)}, as_json)
        sys.exit(1)


@cli.command("status")
@click.argument("task_id")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output structured JSON.")
@click.pass_context
def cmd_status(ctx: click.Context, task_id: str, as_json: bool) -> None:
    """Query the current status and metadata of a task."""
    engine: BridgeEngine = ctx.obj["engine"]
    task = engine.store.get_task(task_id)
    if not task:
        _emit({"ok": False, "error": f"Task '{task_id}' not found."}, as_json)
        sys.exit(1)
    _emit({"ok": True, "task": format_task_payload(task)}, as_json)


@cli.command("wait")
@click.argument("task_id")
@click.option("--timeout", "timeout_sec", type=int, default=1800, show_default=True, help="Maximum wait time in seconds.")
@click.option("--interval", "poll_interval", type=float, default=1.0, show_default=True, help="Polling interval in seconds.")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output structured JSON.")
@click.pass_context
def cmd_wait(ctx: click.Context, task_id: str, timeout_sec: int, poll_interval: float, as_json: bool) -> None:
    """Wait until a background task finishes or requires user action."""
    engine: BridgeEngine = ctx.obj["engine"]
    try:
        task = engine.wait_for_task(task_id, timeout_seconds=timeout_sec, poll_interval=poll_interval)
        _emit({"ok": True, "task": format_task_payload(task)}, as_json)
        if task.status in (TaskStatus.FAILED.value, TaskStatus.CANCELLED.value):
            sys.exit(1)
    except Exception as exc:
        _emit({"ok": False, "error": str(exc)}, as_json)
        sys.exit(1)


@cli.command("fetch")
@click.argument("task_id")
@click.option("--print-report", is_flag=True, default=False, help="Include full Markdown report in output.")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output structured JSON.")
@click.pass_context
def cmd_fetch(ctx: click.Context, task_id: str, print_report: bool, as_json: bool) -> None:
    """Fetch or re-synchronize task results to local files without re-submitting."""
    engine: BridgeEngine = ctx.obj["engine"]
    try:
        task = engine.fetch_task(task_id)
        payload = format_task_payload(task, include_report=print_report or as_json)
        _emit({"ok": True, "task": payload}, as_json)
        if print_report and not as_json and payload.get("report_markdown"):
            click.echo("\n" + payload["report_markdown"])
    except Exception as exc:
        _emit({"ok": False, "error": str(exc)}, as_json)
        sys.exit(1)


@cli.command("list")
@click.option("--mode", type=click.Choice(["deep-research", "app-task"]), default=None, help="Filter by task mode.")
@click.option(
    "--status",
    type=click.Choice(["queued", "running", "completed", "failed", "needs_user_action", "cancelled"]),
    default=None,
    help="Filter by task status.",
)
@click.option("--limit", type=int, default=25, show_default=True, help="Maximum number of tasks to list.")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output structured JSON.")
@click.pass_context
def cmd_list(ctx: click.Context, mode: Optional[str], status: Optional[str], limit: int, as_json: bool) -> None:
    """View task history stored in SQLite."""
    engine: BridgeEngine = ctx.obj["engine"]
    tasks = engine.store.list_tasks(mode=mode, status=status, limit=limit)
    if as_json:
        _emit(
            {
                "ok": True,
                "count": len(tasks),
                "tasks": [format_task_payload(t) for t in tasks],
            },
            as_json=True,
        )
        return

    if not tasks:
        click.echo("No tasks found.")
        return

    click.echo(f"{'TASK ID':<26} {'MODE':<15} {'BACKEND':<8} {'STATUS':<19} {'CREATED AT':<26}")
    click.echo("-" * 98)
    for t in tasks:
        click.echo(f"{t.task_id:<26} {t.mode:<15} {t.backend:<8} {t.status:<19} {t.created_at:<26}")


@cli.command("resume")
@click.argument("task_id")
@click.option("--background/--foreground", default=False, show_default=True, help="Run resumed task in background.")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output structured JSON.")
@click.pass_context
def cmd_resume(ctx: click.Context, task_id: str, background: bool, as_json: bool) -> None:
    """Safely resume an interrupted or needs_user_action task."""
    engine: BridgeEngine = ctx.obj["engine"]
    try:
        task = engine.resume_task(task_id, background=background)
        _emit({"ok": True, "task": format_task_payload(task)}, as_json)
    except Exception as exc:
        _emit({"ok": False, "error": str(exc)}, as_json)
        sys.exit(1)


@cli.command("doctor")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output structured JSON.")
@click.pass_context
def cmd_doctor(ctx: click.Context, as_json: bool) -> None:
    """Inspect local environment, SQLite storage, browser session readiness, and API configuration."""
    cfg: BridgeConfig = ctx.obj["config"]
    engine: BridgeEngine = ctx.obj["engine"]

    web_health = engine.get_backend("web").check_health()
    api_health = engine.get_backend("api").check_health()

    report = {
        "ok": True,
        "system": {
            "os": platform.system(),
            "release": platform.release(),
            "python_version": platform.python_version(),
            "python_executable": sys.executable,
        },
        "storage": {
            "base_dir": str(cfg.base_dir),
            "db_path": str(cfg.db_path),
            "db_exists": cfg.db_path.exists(),
            "results_dir": str(cfg.results_dir),
            "logs_dir": str(cfg.logs_dir),
        },
        "default_backend": cfg.default_backend,
        "web_backend": web_health,
        "api_backend": api_health,
    }

    if as_json:
        _emit(report, as_json=True)
        return

    click.secho("=== Gemini Bridge Environment & Login Doctor ===", bold=True)
    click.echo(f"OS / Python       : {report['system']['os']} ({report['system']['release']}) / Python {report['system']['python_version']}")
    click.echo(f"SQLite Database   : {report['storage']['db_path']} (exists: {report['storage']['db_exists']})")
    click.echo(f"Results Directory : {report['storage']['results_dir']}")
    click.echo(f"Default Backend   : {cfg.default_backend}")
    click.echo("")
    click.secho("[1] Web Browser Backend (Priority 1: Reuse Gemini Web Account)", bold=True)
    click.echo(f"  Connection Mode : {web_health['mode']}")
    click.echo(f"  Status          : {'READY' if web_health['ready'] else 'NEEDS SETUP / LOGIN'}")
    click.echo(f"  Details         : {web_health['reason']}")
    click.echo(f"  Playwright      : {'Installed' if web_health['environment']['playwright_installed'] else 'Not installed'}")
    click.echo(f"  Browsers Found  : {', '.join(web_health['environment']['browsers_found']) or 'None detected'}")
    if not web_health["ready"]:
        click.secho("  macOS Quick Start to Connect Existing Session:", fg="cyan")
        click.echo(f"    {web_health['macos_launch_hint']}")
    click.echo("")
    click.secho("[2] Official Gemini API Backend (Priority 2: Optional Paid Fallback)", bold=True)
    click.echo(f"  API Key Set     : {'Yes' if api_health['api_key_configured'] else 'No'} (env: {api_health['api_key_env']})")
    click.echo(f"  Deep Research   : {api_health['deep_research_agent']} (via Interactions API)")
    click.echo(f"  App Tasks API   : Unavailable (No public API for consumer Gemini App Tasks/Scheduled Actions)")
    click.echo(f"  Billing Notice  : {api_health['billing_notice']}")


@cli.command("mcp")
@click.pass_context
def cmd_mcp(ctx: click.Context) -> None:
    """Start the STDIO Model Context Protocol (MCP) server for Claude Code / Codex / Cursor."""
    cfg: BridgeConfig = ctx.obj["config"]
    server = GeminiBridgeMcpServer(cfg)
    server.serve_stdio()


@cli.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True, help="Loopback address to bind (strictly 127.0.0.1).")
@click.option("--port", type=int, default=8765, show_default=True, help="Local port to listen on.")
@click.pass_context
def cmd_serve(ctx: click.Context, host: str, port: int) -> None:
    """Start the optional local-only HTTP server (bound strictly to 127.0.0.1)."""
    cfg: BridgeConfig = ctx.obj["config"]
    click.echo(f"Starting Gemini Bridge local HTTP server on http://{host}:{port} ...")
    run_local_http_server(cfg, host=host, port=port)


def main() -> None:
    cli(obj={})


if __name__ == "__main__":
    main()
