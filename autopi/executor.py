"""Local Worker Execution Engine (Pi Agent Protocol).

Executes local sandboxed commands, manages file creation and unit tests,
captures execution telemetry, and enforces process group confinement.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple


SENSITIVE_REDACTION_PATTERNS = [
    (re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE), "Bearer ***REDACTED***"),
    (re.compile(r"(api[_-]?key|token|secret|password|cookie)\s*[:=]\s*['\"]?([^'\"\s&;]+)['\"]?", re.IGNORECASE), r"\1=***REDACTED***"),
    (re.compile(r"AIza[0-9A-Za-z\-_]{35}"), "***REDACTED_API_KEY***"),
    (re.compile(r"sk-[a-zA-Z0-9\-_]{20,}"), "***REDACTED_SECRET***"),
]

BLOCKED_COMMANDS = {
    "rm -rf /",
    "mkfs",
    ":(){ :|:& };:",
    "dd if=",
    "chmod -R 777 /",
    "> /dev/sda",
}


def redact_secrets(text: str) -> str:
    cleaned = text
    for pattern, replacement in SENSITIVE_REDACTION_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


class LocalWorkerExecutor:
    def __init__(self, workspace_root: Path | str) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self._active_proc: Optional[subprocess.Popen] = None

    def execute_action(
        self,
        file_writes: Optional[Dict[str, str]] = None,
        commands: Optional[List[str]] = None,
        test_commands: Optional[List[str]] = None,
        timeout_sec: float = 60.0,
    ) -> Dict[str, Any]:
        t0 = time.time()
        created_files: List[Dict[str, Any]] = []
        stdout_parts: List[str] = []
        stderr_parts: List[str] = []
        overall_exit_code = 0

        # 1. Write or patch files
        if file_writes:
            for rel_path, content in file_writes.items():
                target = (self.workspace_root / rel_path).resolve()
                # Path traversal guard
                if not str(target).startswith(str(self.workspace_root)):
                    raise PermissionError(f"Security violation: path '{rel_path}' escapes workspace sandbox.")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                raw_bytes = target.read_bytes()
                created_files.append({
                    "file_path": rel_path,
                    "file_size": len(raw_bytes),
                    "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                })

        # 2. Execute main commands
        if commands:
            for cmd in commands:
                ret, out, err = self._run_command(cmd, timeout_sec=timeout_sec)
                stdout_parts.append(f"$ {cmd}\n{out}")
                if err:
                    stderr_parts.append(err)
                if ret != 0:
                    overall_exit_code = ret
                    break

        # 3. Execute test commands
        if overall_exit_code == 0 and test_commands:
            for tcmd in test_commands:
                ret, out, err = self._run_command(tcmd, timeout_sec=timeout_sec)
                stdout_parts.append(f"$ {tcmd} [TEST]\n{out}")
                if err:
                    stderr_parts.append(err)
                if ret != 0:
                    overall_exit_code = ret
                    break

        duration = round(time.time() - t0, 3)
        return {
            "exit_code": overall_exit_code,
            "stdout": redact_secrets("\n".join(stdout_parts)),
            "stderr": redact_secrets("\n".join(stderr_parts)),
            "created_files": created_files,
            "duration_sec": duration,
        }

    def _run_command(self, cmd: str, timeout_sec: float = 60.0) -> Tuple[int, str, str]:
        # Block dangerous commands
        for bad in BLOCKED_COMMANDS:
            if bad in cmd:
                return 126, "", f"Command blocked by safety security sandbox: '{cmd}'"

        env = os.environ.copy()
        src_path = self.workspace_root / "src"
        env["PYTHONPATH"] = f"{self.workspace_root}:{src_path}:{env.get('PYTHONPATH', '')}"

        try:
            proc = subprocess.Popen(
                cmd,
                shell=True,
                cwd=str(self.workspace_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,  # Process group for clean SIGKILL
                env=env,
            )
            self._active_proc = proc
            out, err = proc.communicate(timeout=timeout_sec)
            self._active_proc = None
            return proc.returncode, out or "", err or ""
        except subprocess.TimeoutExpired:
            self.emergency_kill()
            return 124, "", f"Command timed out after {timeout_sec}s."
        except Exception as exc:
            self._active_proc = None
            return 1, "", str(exc)

    def emergency_kill(self) -> None:
        """Kills the entire active process group immediately."""
        if self._active_proc and self._active_proc.poll() is None:
            try:
                pgid = os.getpgid(self._active_proc.pid)
                os.killpg(pgid, 9)
            except Exception:
                try:
                    self._active_proc.kill()
                except Exception:
                    pass
            self._active_proc = None
