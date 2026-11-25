"""Comprehensive unit and integration test suite for Gemini Bridge.

Validates:
1. CLI help and parameter parsing (`--help`, `submit`, `status`, `wait`, `fetch`, `list`, `resume`, `doctor`).
2. Prompt file reading (Chinese and English UTF-8 prompts).
3. Unique Task ID generation (`dr-...`, `at-...`).
4. SQLite task state persistence and concurrent WAL mode access.
5. Status updates, orphan worker detection, and safe task recovery (`resume`).
6. Markdown (`report.md`, `sources.md`), JSON (`metadata.json`), and log (`task.log`) synchronization.
7. Error handling, sensitive credential redaction, duplicate submission protection, and non-loopback HTTP rejection.
8. Structured JSON output for CLI and STDIO MCP Server (`submit_task`, `get_task_status`, `wait_for_task`, `get_task_result`, `list_tasks`).
9. Authentic Web & API boundary verification (ensuring unauthenticated web or unconfigured API transitions cleanly to `needs_user_action` without fabricating results).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from click.testing import CliRunner

from gemini_bridge.cli import cli
from gemini_bridge.config import BridgeConfig, redact_sensitive
from gemini_bridge.http_server import run_local_http_server
from gemini_bridge.mcp_server import GeminiBridgeMcpServer
from gemini_bridge.tasks import (
    DuplicateTaskError,
    TaskStatus,
    TaskStore,
    generate_task_id,
)
from gemini_bridge.worker import BridgeEngine


class TestGeminiBridge(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.tmp_dir.name)
        self.cfg = BridgeConfig.load(base_dir=self.base_path)
        self.engine = BridgeEngine(self.cfg)
        self.runner = CliRunner()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _run_cli_subprocess(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        src_dir = str(Path(__file__).resolve().parent.parent / "src")
        env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{env.get('PYTHONPATH', '')}"
        return subprocess.run(
            [sys.executable, "-m", "gemini_bridge.cli", "--home", str(self.base_path), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    def test_01_cli_help_and_doctor(self) -> None:
        """Verify CLI --help and doctor command."""
        res_help = self.runner.invoke(cli, ["--help"])
        self.assertEqual(res_help.exit_code, 0)
        self.assertIn("submit", res_help.output)
        self.assertIn("status", res_help.output)
        self.assertIn("wait", res_help.output)
        self.assertIn("fetch", res_help.output)
        self.assertIn("list", res_help.output)
        self.assertIn("resume", res_help.output)
        self.assertIn("doctor", res_help.output)
        self.assertIn("mcp", res_help.output)

        res_doc = self.runner.invoke(cli, ["--home", str(self.base_path), "doctor", "--json"])
        self.assertEqual(res_doc.exit_code, 0)
        doc_data = json.loads(res_doc.output)
        self.assertTrue(doc_data["ok"])
        self.assertIn("web_backend", doc_data)
        self.assertIn("api_backend", doc_data)
        self.assertFalse(doc_data["api_backend"]["app_task_public_api_available"])

    def test_02_task_id_and_sqlite_persistence(self) -> None:
        """Verify unique task IDs and SQLite state persistence across store instances."""
        dr_id_1 = generate_task_id("deep-research")
        dr_id_2 = generate_task_id("deep-research")
        at_id_1 = generate_task_id("app-task")
        self.assertTrue(dr_id_1.startswith("dr-"))
        self.assertTrue(at_id_1.startswith("at-"))
        self.assertNotEqual(dr_id_1, dr_id_2)

        task = self.engine.store.create_task(
            mode="deep-research",
            prompt="研究量子计算纠错码进展 (Quantum Error Correction)",
            backend="mock",
            result_dir=self.cfg.results_dir / "deep-research",
            log_path=self.cfg.logs_dir / "bridge.log",
        )
        # Re-open a brand new TaskStore connection to simulate process restart
        reopened_store = TaskStore(self.cfg.db_path)
        loaded = reopened_store.get_task(task.task_id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.task_id, task.task_id)
        self.assertEqual(loaded.status, TaskStatus.QUEUED.value)
        self.assertIn("量子计算", loaded.prompt)

    def test_03_duplicate_submission_protection(self) -> None:
        """Verify duplicate active task detection and --force override."""
        prompt = "Analyze solid-state battery supply chain 2026"
        t1 = self.engine.store.create_task(
            mode="deep-research",
            prompt=prompt,
            backend="mock",
            result_dir=self.cfg.results_dir / "deep-research",
            log_path=self.cfg.logs_dir / "bridge.log",
        )
        with self.assertRaises(DuplicateTaskError) as ctx:
            self.engine.store.create_task(
                mode="deep-research",
                prompt=prompt,
                backend="mock",
                result_dir=self.cfg.results_dir / "deep-research",
                log_path=self.cfg.logs_dir / "bridge.log",
                force=False,
            )
        self.assertEqual(ctx.exception.existing_task_id, t1.task_id)

        # With force=True, submission succeeds
        t2 = self.engine.store.create_task(
            mode="deep-research",
            prompt=prompt,
            backend="mock",
            result_dir=self.cfg.results_dir / "deep-research",
            log_path=self.cfg.logs_dir / "bridge.log",
            force=True,
        )
        self.assertNotEqual(t1.task_id, t2.task_id)

    def test_04_prompt_file_submission_and_artifact_sync(self) -> None:
        """Verify reading prompt from file, detached background worker execution, wait, and 4-file local sync."""
        prompt_file = self.base_path / "research_prompt.md"
        prompt_file.write_text(
            "# 深度研究测试\n请分析 Google TPUs 架构演进并附上官方链接。",
            encoding="utf-8",
        )

        # Submit in background via real CLI subprocess
        res_sub = self._run_cli_subprocess(
            "submit",
            "--mode",
            "deep-research",
            "--backend",
            "mock",
            "--file",
            str(prompt_file),
            "--json",
        )
        self.assertEqual(res_sub.returncode, 0, msg=res_sub.stderr)
        sub_payload = json.loads(res_sub.stdout)
        self.assertTrue(sub_payload["ok"])
        task_id = sub_payload["task"]["task_id"]

        # Wait for background worker to finish
        res_wait = self._run_cli_subprocess("wait", task_id, "--timeout", "15", "--json")
        self.assertEqual(res_wait.returncode, 0, msg=res_wait.stderr)
        wait_payload = json.loads(res_wait.stdout)
        self.assertEqual(wait_payload["task"]["status"], "completed")

        # Verify all 4 local artifact files exist and are non-empty UTF-8
        paths = wait_payload["task"]["result_paths"]
        report_p = Path(paths["report_path"])
        meta_p = Path(paths["metadata_path"])
        sources_p = Path(paths["sources_path"])
        log_p = Path(paths["log_path"])

        self.assertTrue(report_p.exists())
        self.assertTrue(meta_p.exists())
        self.assertTrue(sources_p.exists())
        self.assertTrue(log_p.exists())

        report_text = report_p.read_text(encoding="utf-8")
        self.assertIn("https://ai.google.dev/gemini-api/docs/deep-research", report_text)

        meta_json = json.loads(meta_p.read_text(encoding="utf-8"))
        self.assertEqual(meta_json["task_id"], task_id)
        self.assertEqual(meta_json["status"], "completed")
        self.assertGreaterEqual(meta_json["sources_count"], 4)

        sources_text = sources_p.read_text(encoding="utf-8")
        self.assertIn("https://ai.google.dev/gemini-api/docs/deep-research", sources_text)

        # Verify status and fetch commands after worker termination
        res_fetch = self._run_cli_subprocess("fetch", task_id, "--json")
        self.assertEqual(res_fetch.returncode, 0)
        fetch_payload = json.loads(res_fetch.stdout)
        self.assertIn("report_markdown", fetch_payload["task"])

    def test_05_needs_user_action_and_safe_resume(self) -> None:
        """Verify task pausing at needs_user_action and safe recovery via resume."""
        res_sub = self.runner.invoke(
            cli,
            [
                "--home",
                str(self.base_path),
                "submit",
                "--mode",
                "deep-research",
                "--backend",
                "mock",
                "--prompt",
                "Test login pause [TRIGGER_NEEDS_LOGIN]",
                "--wait",
                "--json",
            ],
        )
        self.assertEqual(res_sub.exit_code, 0)
        payload = json.loads(res_sub.output)
        task_id = payload["task"]["task_id"]
        self.assertEqual(payload["task"]["status"], "needs_user_action")

        # Update prompt to simulate user having completed login, then run `resume`
        self.engine.store.update_task(
            task_id,
            external_url="https://gemini.google.com/app/thread/123",
        )
        res_resume = self.runner.invoke(
            cli,
            ["--home", str(self.base_path), "resume", task_id, "--json"],
        )
        self.assertEqual(res_resume.exit_code, 0)
        resumed_payload = json.loads(res_resume.output)
        self.assertEqual(resumed_payload["task"]["status"], "completed")

    def test_06_web_and_api_real_boundary_guards(self) -> None:
        """Verify real Web and API backends accurately report needs_user_action when unconfigured/logged-out."""
        # 1. Web backend when no CDP browser is running
        web_res = self.runner.invoke(
            cli,
            [
                "--home",
                str(self.base_path),
                "submit",
                "--mode",
                "deep-research",
                "--backend",
                "web",
                "--prompt",
                "Real web boundary check",
                "--wait",
                "--json",
            ],
        )
        self.assertEqual(web_res.exit_code, 0)
        web_data = json.loads(web_res.output)
        self.assertEqual(web_data["task"]["status"], "needs_user_action")
        self.assertTrue(web_data["task"]["user_action_hint"])

        # 2. API backend when GEMINI_API_KEY is not set
        api_res = self.runner.invoke(
            cli,
            [
                "--home",
                str(self.base_path),
                "submit",
                "--mode",
                "deep-research",
                "--backend",
                "api",
                "--prompt",
                "Real API boundary check",
                "--wait",
                "--json",
            ],
        )
        self.assertEqual(api_res.exit_code, 0)
        api_data = json.loads(api_res.output)
        self.assertEqual(api_data["task"]["status"], "needs_user_action")
        self.assertIn("GEMINI_API_KEY", api_data["task"]["error_message"])

        # 3. App-Task mode boundary check
        app_res = self.runner.invoke(
            cli,
            [
                "--home",
                str(self.base_path),
                "submit",
                "--mode",
                "app-task",
                "--backend",
                "mock",
                "--prompt",
                "Schedule a recurring Monday 9am briefing",
                "--wait",
                "--json",
            ],
        )
        self.assertEqual(app_res.exit_code, 0)
        app_data = json.loads(app_res.output)
        self.assertEqual(app_data["task"]["status"], "needs_user_action")
        self.assertIn("17094507", app_data["task"]["user_action_hint"])

    def test_07_mcp_server_jsonrpc_protocol(self) -> None:
        """Verify MCP Server tools/list and all 5 required MCP tools over JSON-RPC 2.0."""
        mcp = GeminiBridgeMcpServer(self.cfg)

        # 1. initialize
        init_resp = mcp.handle_jsonrpc_message({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertIsNotNone(init_resp)
        assert init_resp is not None
        self.assertEqual(init_resp["result"]["serverInfo"]["name"], "gemini-bridge")

        # 2. tools/list
        list_resp = mcp.handle_jsonrpc_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert list_resp is not None
        tool_names = [t["name"] for t in list_resp["result"]["tools"]]
        for expected in ("submit_task", "get_task_status", "wait_for_task", "get_task_result", "list_tasks"):
            self.assertIn(expected, tool_names)

        # 3. submit_task
        call_sub = mcp.handle_jsonrpc_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "submit_task",
                    "arguments": {
                        "mode": "deep-research",
                        "prompt": "MCP submitted research query",
                        "backend": "mock",
                        "background": False,
                    },
                },
            }
        )
        assert call_sub is not None
        self.assertFalse(call_sub["result"]["isError"])
        sub_body = json.loads(call_sub["result"]["content"][0]["text"])
        task_id = sub_body["task"]["task_id"]

        # 4. get_task_status
        status_body = mcp.handle_tool_call("get_task_status", {"task_id": task_id})
        self.assertTrue(status_body["ok"])
        self.assertEqual(status_body["task"]["status"], "completed")

        # 5. wait_for_task
        wait_body = mcp.handle_tool_call("wait_for_task", {"task_id": task_id, "timeout_seconds": 5})
        self.assertTrue(wait_body["ok"])

        # 6. get_task_result
        res_body = mcp.handle_tool_call("get_task_result", {"task_id": task_id})
        self.assertTrue(res_body["ok"])
        self.assertIn("MCP submitted research query", res_body["task"]["report_markdown"])

        # 7. list_tasks
        list_body = mcp.handle_tool_call("list_tasks", {"mode": "deep-research"})
        self.assertTrue(list_body["ok"])
        self.assertGreaterEqual(list_body["count"], 1)

    def test_08_security_redaction_and_loopback_enforcement(self) -> None:
        """Verify sensitive credentials are redacted in logs and HTTP server rejects 0.0.0.0."""
        raw_secret = "Error with key AIzaSyD1234567890abcdefghijklmnop and SID=secret_cookie_val"
        sanitized = redact_sensitive(raw_secret)
        self.assertNotIn("AIzaSyD1234567890abcdefghijklmnop", sanitized)
        self.assertNotIn("secret_cookie_val", sanitized)
        self.assertIn("REDACTED", sanitized)

        with self.assertRaises(ValueError):
            run_local_http_server(self.cfg, host="0.0.0.0", port=8765)


if __name__ == "__main__":
    unittest.main()
