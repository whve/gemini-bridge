"""Supervisor Web Console Server & REST API for AutoPi (Pure Standard Library).

Runs strictly on localhost (127.0.0.1) without third-party web dependencies.
Provides real-time telemetry, live dual-agent feed, and human boss override controls.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

from .daemon import AutonomyDaemon

logger = logging.getLogger("autopi.server")


class SupervisorRequestHandler(BaseHTTPRequestHandler):
    daemon: AutonomyDaemon
    static_dir: Path

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress noisy standard request logging

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            index_file = self.static_dir / "index.html"
            if index_file.exists():
                content = index_file.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            self._send_text("AutoPi Supervisor Console UI file missing.", status=404)
            return

        if path == "/api/status":
            telemetry = self.daemon.get_status_telemetry()
            self._send_json({"ok": True, "data": telemetry})
            return

        if path == "/api/tasks":
            active_goal = self.daemon.engine.db.get_active_goal()
            tasks = self.daemon.engine.db.list_sub_tasks(active_goal["id"]) if active_goal else []
            self._send_json({"ok": True, "tasks": tasks})
            return

        if path == "/api/iterations":
            iters = self.daemon.engine.db.list_recent_iterations(limit=25)
            self._send_json({"ok": True, "iterations": iters})
            return

        if path == "/api/artifacts":
            artifacts = self.daemon.engine.db.list_artifacts()
            self._send_json({"ok": True, "artifacts": artifacts})
            return

        self._send_json({"ok": False, "error": "Not Found"}, status=404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._read_json_body()

        if path == "/api/control/pause":
            self.daemon.pause()
            self._send_json({"ok": True, "status": "paused", "message": "Loop paused."})
            return

        if path == "/api/control/resume":
            self.daemon.resume()
            self._send_json({"ok": True, "status": "running", "message": "Loop resumed."})
            return

        if path == "/api/control/stop":
            self.daemon.emergency_stop()
            self._send_json({"ok": True, "status": "emergency_stopped", "message": "Emergency stop executed."})
            return

        if path == "/api/control/intervene":
            instruction = str(body.get("instruction", "")).strip()
            priority = str(body.get("priority", "critical")).strip()
            if not instruction:
                self._send_json({"ok": False, "error": "Empty instruction."}, status=400)
                return
            import uuid
            inter_id = f"boss_{uuid.uuid4().hex[:8]}"
            self.daemon.engine.db.inject_boss_intervention(inter_id, instruction, priority=priority)
            self._send_json({"ok": True, "intervention_id": inter_id, "message": "Directive injected into loop context."})
            return

        if path == "/api/control/goal":
            title = str(body.get("title", "")).strip()
            desc = str(body.get("description", "")).strip()
            if not title:
                self._send_json({"ok": False, "error": "Goal title is required."}, status=400)
                return
            goal = self.daemon.engine.init_master_goal(title, desc)
            self._send_json({"ok": True, "goal": goal, "message": "New master goal initialized."})
            return

        self._send_json({"ok": False, "error": "Endpoint Not Found"}, status=404)

    def _read_json_body(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 0:
                raw = self.rfile.read(length).decode("utf-8")
                return json.loads(raw)
        except Exception:
            pass
        return {}

    def _send_json(self, data: Any, status: int = 200) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def _send_text(self, text: str, status: int = 200) -> None:
        payload = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def run_supervisor_server(
    daemon: AutonomyDaemon,
    host: str = "127.0.0.1",
    port: int = 9100,
    static_dir: Optional[Path] = None,
) -> ThreadingHTTPServer:
    static_path = static_dir or (Path(__file__).parent / "static")

    class BoundHandler(SupervisorRequestHandler):
        pass

    BoundHandler.daemon = daemon
    BoundHandler.static_dir = static_path

    server = ThreadingHTTPServer((host, port), BoundHandler)
    logger.info("AutoPi Supervisor Console running on http://%s:%d", host, port)
    return server
