"""Optional Local-Only HTTP Server (`127.0.0.1`) for Agent REST calls.

Strictly refuses to bind to `0.0.0.0` or public interfaces to prevent unauthorized exposure.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from .config import BridgeConfig
from .mcp_server import GeminiBridgeMcpServer


def run_local_http_server(config: BridgeConfig, host: str = "127.0.0.1", port: int = 8765) -> None:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(
            f"Security violation: HTTP server refuses to bind to non-loopback address '{host}'. "
            "Only 127.0.0.1 or localhost is permitted."
        )

    mcp = GeminiBridgeMcpServer(config)

    class BridgeHTTPHandler(BaseHTTPRequestHandler):
        def _send_json(self, status_code: int, data: dict) -> None:
            body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._send_json(200, {"ok": True, "service": "gemini-bridge", "host": host, "port": port})
            elif parsed.path == "/tasks":
                qs = parse_qs(parsed.query)
                res = mcp.handle_tool_call(
                    "list_tasks",
                    {
                        "mode": qs.get("mode", [None])[0],
                        "status": qs.get("status", [None])[0],
                        "limit": int(qs.get("limit", ["20"])[0]),
                    },
                )
                self._send_json(200 if res.get("ok") else 400, res)
            elif parsed.path.startswith("/tasks/"):
                parts = [p for p in parsed.path.split("/") if p]
                task_id = parts[1] if len(parts) >= 2 else ""
                if len(parts) == 3 and parts[2] == "result":
                    res = mcp.handle_tool_call("get_task_result", {"task_id": task_id})
                else:
                    res = mcp.handle_tool_call("get_task_status", {"task_id": task_id})
                self._send_json(200 if res.get("ok") else 404, res)
            else:
                self._send_json(404, {"ok": False, "error": "Not found"})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            try:
                body = json.loads(raw)
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": f"Invalid JSON: {exc}"})
                return

            if self.path == "/tasks":
                res = mcp.handle_tool_call("submit_task", body)
                self._send_json(200 if res.get("ok") else 400, res)
            else:
                self._send_json(404, {"ok": False, "error": "Not found"})

    server = HTTPServer((host, port), BridgeHTTPHandler)
    try:
        server.serve_forever()
    finally:
        server.server_close()
