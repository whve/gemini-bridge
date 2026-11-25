"""Configuration management and sensitive data sanitization for Gemini Bridge."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

# Patterns for redacting sensitive credentials in logs and error messages
_SENSITIVE_PATTERNS = [
    (re.compile(r"(AIza[0-9A-Za-z\-_]{20,})"), "AIza***REDACTED***"),
    (re.compile(r"(x-goog-api-key\s*[:=]\s*)([^\s,;\"']+)", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(Authorization\s*[:=]\s*Bearer\s+)([^\s,;\"']+)", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(__Secure-[0-9A-Za-z\-_]+|SID|HSID|SSID|APISID|SAPISID)=([^;\s\"']+)"), r"\1=***REDACTED***"),
    (re.compile(r"(password|passwd|secret|api_key|token)\s*[:=]\s*['\"]?([^'\"\s,;]+)['\"]?", re.IGNORECASE), r"\1=***REDACTED***"),
]


def redact_sensitive(text: str) -> str:
    """Redact API keys, cookies, tokens, and passwords from log/error strings."""
    if not text:
        return ""
    sanitized = str(text)
    for pattern, replacement in _SENSITIVE_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


@dataclass
class BrowserConfig:
    engine: str = "playwright"  # "playwright" | "selenium"
    mode: str = "cdp"  # "cdp" | "persistent_profile"
    cdp_url: str = "http://127.0.0.1:9222"
    user_data_dir: str = "~/.gemini-bridge/chrome-profile"
    headless: bool = False
    gemini_url: str = "https://gemini.google.com/app"
    auto_confirm_plan: bool = True
    poll_interval_seconds: int = 10
    task_timeout_seconds: int = 1800
    stability_checks: int = 2

    @property
    def resolved_user_data_dir(self) -> Path:
        return Path(os.path.expanduser(self.user_data_dir)).resolve()


@dataclass
class ApiConfig:
    enabled: bool = False
    api_key_env: str = "GEMINI_API_KEY"
    api_key: Optional[str] = None
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    deep_research_agent: str = "deep-research-preview-04-2026"
    app_task_fallback_agent: str = "antigravity-preview-09-2026"
    allow_api_fallback_for_app_task: bool = False
    poll_interval_seconds: int = 10
    task_timeout_seconds: int = 3600

    def get_api_key(self) -> Optional[str]:
        if self.api_key:
            return self.api_key
        return os.environ.get(self.api_key_env) or os.environ.get("GOOGLE_API_KEY")


@dataclass
class HttpServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass
class BridgeConfig:
    base_dir: Path = field(default_factory=lambda: Path.cwd())
    default_backend: str = "web"  # "web" | "api" | "mock"
    data_dir: Path = field(default_factory=lambda: Path("./data"))
    results_dir: Path = field(default_factory=lambda: Path("./results"))
    db_path: Path = field(default_factory=lambda: Path("./data/tasks.db"))
    logs_dir: Path = field(default_factory=lambda: Path("./data/logs"))
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    http_server: HttpServerConfig = field(default_factory=HttpServerConfig)

    def resolve_paths(self) -> None:
        """Resolve relative paths against base_dir and ensure directories exist."""
        if not self.data_dir.is_absolute():
            self.data_dir = (self.base_dir / self.data_dir).resolve()
        if not self.results_dir.is_absolute():
            self.results_dir = (self.base_dir / self.results_dir).resolve()
        if not self.db_path.is_absolute():
            self.db_path = (self.base_dir / self.db_path).resolve()
        if not self.logs_dir.is_absolute():
            self.logs_dir = (self.base_dir / self.logs_dir).resolve()

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.results_dir / "deep-research").mkdir(parents=True, exist_ok=True)
        (self.results_dir / "app-tasks").mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def load(cls, config_path: Optional[str | Path] = None, base_dir: Optional[Path] = None) -> "BridgeConfig":
        """Load configuration from YAML file, environment variables, and defaults."""
        resolved_base = Path(base_dir or os.environ.get("GEMINI_BRIDGE_HOME") or Path.cwd()).resolve()

        raw: Dict[str, Any] = {}
        candidate_paths = []
        if config_path:
            candidate_paths.append(Path(config_path))
        elif os.environ.get("GEMINI_BRIDGE_CONFIG"):
            candidate_paths.append(Path(os.environ["GEMINI_BRIDGE_CONFIG"]))
        else:
            candidate_paths.extend([
                resolved_base / "config.yaml",
                Path.home() / ".config" / "gemini-bridge" / "config.yaml",
            ])

        for p in candidate_paths:
            if p.exists() and p.is_file():
                with open(p, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f)
                    if isinstance(loaded, dict):
                        raw = loaded
                break

        browser_raw = raw.get("browser", {}) or {}
        api_raw = raw.get("api", {}) or {}
        http_raw = raw.get("http_server", {}) or {}

        browser_cfg = BrowserConfig(
            engine=os.environ.get("GEMINI_BRIDGE_BROWSER_ENGINE", browser_raw.get("engine", "playwright")),
            mode=os.environ.get("GEMINI_BRIDGE_BROWSER_MODE", browser_raw.get("mode", "cdp")),
            cdp_url=os.environ.get("GEMINI_BRIDGE_CDP_URL", browser_raw.get("cdp_url", "http://127.0.0.1:9222")),
            user_data_dir=os.environ.get(
                "GEMINI_BRIDGE_USER_DATA_DIR",
                browser_raw.get("user_data_dir", "~/.gemini-bridge/chrome-profile"),
            ),
            headless=str(os.environ.get("GEMINI_BRIDGE_HEADLESS", browser_raw.get("headless", False))).lower()
            in ("1", "true", "yes"),
            gemini_url=browser_raw.get("gemini_url", "https://gemini.google.com/app"),
            auto_confirm_plan=bool(browser_raw.get("auto_confirm_plan", True)),
            poll_interval_seconds=int(browser_raw.get("poll_interval_seconds", 10)),
            task_timeout_seconds=int(browser_raw.get("task_timeout_seconds", 1800)),
            stability_checks=int(browser_raw.get("stability_checks", 2)),
        )

        api_key_env = api_raw.get("api_key_env", "GEMINI_API_KEY")
        env_key = os.environ.get(api_key_env) or os.environ.get("GEMINI_API_KEY")
        api_enabled = bool(api_raw.get("enabled", False)) or bool(
            os.environ.get("GEMINI_BRIDGE_ENABLE_API", "").lower() in ("1", "true", "yes")
        )

        api_cfg = ApiConfig(
            enabled=api_enabled,
            api_key_env=api_key_env,
            api_key=env_key,
            base_url=api_raw.get("base_url", "https://generativelanguage.googleapis.com/v1beta"),
            deep_research_agent=api_raw.get("deep_research_agent", "deep-research-preview-04-2026"),
            app_task_fallback_agent=api_raw.get("app_task_fallback_agent", "antigravity-preview-09-2026"),
            allow_api_fallback_for_app_task=bool(api_raw.get("allow_api_fallback_for_app_task", False)),
            poll_interval_seconds=int(api_raw.get("poll_interval_seconds", 10)),
            task_timeout_seconds=int(api_raw.get("task_timeout_seconds", 3600)),
        )

        http_cfg = HttpServerConfig(
            host=http_raw.get("host", "127.0.0.1"),
            port=int(http_raw.get("port", 8765)),
        )

        cfg = cls(
            base_dir=resolved_base,
            default_backend=os.environ.get("GEMINI_BRIDGE_BACKEND", raw.get("default_backend", "web")),
            data_dir=Path(os.environ.get("GEMINI_BRIDGE_DATA_DIR", raw.get("data_dir", "./data"))),
            results_dir=Path(os.environ.get("GEMINI_BRIDGE_RESULTS_DIR", raw.get("results_dir", "./results"))),
            db_path=Path(os.environ.get("GEMINI_BRIDGE_DB_PATH", raw.get("db_path", "./data/tasks.db"))),
            logs_dir=Path(os.environ.get("GEMINI_BRIDGE_LOGS_DIR", raw.get("logs_dir", "./data/logs"))),
            browser=browser_cfg,
            api=api_cfg,
            http_server=http_cfg,
        )
        cfg.resolve_paths()
        return cfg
