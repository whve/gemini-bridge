"""Web Browser Backend (Priority 1: Reuse existing Gemini web account via Playwright or Selenium).

Connects either to an already-logged-in Chrome CDP session (`--remote-debugging-port=9222`)
or launches a dedicated persistent profile directory (`~/.gemini-bridge/chrome-profile`).
Never reads, copies, or modifies the user's default Chrome profile or Cookie DB without consent,
and never attempts to bypass CAPTCHA/MFA—instead transitioning cleanly to `needs_user_action`.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from ..browser import (
    JS_EXTRACT_REPORT_AND_SOURCES,
    SELECTORS,
    detect_installed_browsers,
    get_macos_cdp_launch_command,
    probe_cdp_endpoint,
)
from ..config import BrowserConfig
from ..tasks import DetectionState, TaskMode, TaskRecord, TaskStatus
from .base import BackendResult, TaskBackend


class WebBrowserBackend(TaskBackend):
    """Browser automation backend for Gemini Deep Research and Gemini App Tasks."""

    name = "web"

    def __init__(self, config: BrowserConfig):
        self.config = config

    def check_health(self) -> Dict[str, Any]:
        env_info = detect_installed_browsers()
        cdp_info = probe_cdp_endpoint(self.config.cdp_url)
        profile_dir = self.config.resolved_user_data_dir

        ready = False
        reason = ""
        if self.config.mode == "cdp":
            if cdp_info["reachable"]:
                ready = True
                reason = f"Connected to active Chrome CDP session at {self.config.cdp_url}"
            else:
                reason = (
                    f"CDP endpoint {self.config.cdp_url} is not reachable. "
                    f"Start Chrome with remote debugging enabled or switch browser.mode to 'persistent_profile'."
                )
        else:
            if env_info["playwright_installed"] or (env_info["selenium_installed"] and env_info["browsers_found"]):
                ready = True
                reason = f"Persistent profile mode ready at {profile_dir}"
            else:
                reason = "Playwright is not installed. Run `pip install 'gemini-bridge[browser]' && playwright install chromium`."

        return {
            "backend": self.name,
            "mode": self.config.mode,
            "engine": self.config.engine,
            "ready": ready,
            "reason": reason,
            "cdp": cdp_info,
            "profile_dir": str(profile_dir),
            "profile_exists": profile_dir.exists(),
            "environment": env_info,
            "macos_launch_hint": get_macos_cdp_launch_command(9222, str(profile_dir)),
        }

    def execute_task(
        self,
        task: TaskRecord,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> BackendResult:
        return self._run_browser_workflow(task, is_New_submission=True, on_progress=on_progress)

    def poll_or_fetch(
        self,
        task: TaskRecord,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> BackendResult:
        if not task.external_url:
            return BackendResult(
                status=TaskStatus.NEEDS_USER_ACTION,
                detection_state=DetectionState.UNKNOWN,
                error_message=(
                    "Cannot safely resume or fetch web task without a recorded thread URL (`external_url`). "
                    "Refusing to automatically re-submit to prevent duplicate task execution."
                ),
                user_action_hint=(
                    "Check your Gemini web conversation history at https://gemini.google.com/app. "
                    "If the task was never created, run `gemini-bridge submit --force ...`."
                ),
            )
        return self._run_browser_workflow(task, is_New_submission=False, on_progress=on_progress)

    def _run_browser_workflow(
        self,
        task: TaskRecord,
        is_New_submission: bool,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> BackendResult:
        # Check if Playwright is available
        try:
            from playwright.sync_api import sync_playwright  # type: ignore

            return self._execute_with_playwright(sync_playwright, task, is_New_submission, on_progress)
        except ImportError:
            return BackendResult(
                status=TaskStatus.NEEDS_USER_ACTION,
                detection_state=DetectionState.UNKNOWN,
                error_message=(
                    "Playwright Python package is not installed in the current environment, "
                    "or no active Chrome CDP session was found."
                ),
                user_action_hint=(
                    "On your MacBook:\n"
                    "1. Install Playwright: `pip install 'gemini-bridge[browser]' && playwright install chromium`\n"
                    "2. Launch Chrome with your dedicated profile:\n"
                    f"   {get_macos_cdp_launch_command(9222, str(self.config.resolved_user_data_dir))}\n"
                    "3. Sign in to https://gemini.google.com/app once in that window, then run:\n"
                    f"   `gemini-bridge resume {task.task_id}`"
                ),
            )
        except Exception as exc:
            return BackendResult(
                status=TaskStatus.FAILED,
                detection_state=DetectionState.DOM_CHANGED_OR_UNPARSEABLE,
                error_message=f"Browser execution error: {exc}",
                user_action_hint="Run `gemini-bridge doctor` to inspect browser connectivity and logs.",
            )

    def _execute_with_playwright(
        self,
        sync_playwright: Any,
        task: TaskRecord,
        is_new_submission: bool,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> BackendResult:
        with sync_playwright() as p:
            browser = None
            context = None
            close_browser_when_done = False

            try:
                if self.config.mode == "cdp":
                    cdp_check = probe_cdp_endpoint(self.config.cdp_url)
                    if not cdp_check["reachable"]:
                        return BackendResult(
                            status=TaskStatus.NEEDS_USER_ACTION,
                            detection_state=DetectionState.UNKNOWN,
                            error_message=f"Cannot connect to Chrome CDP at {self.config.cdp_url}: {cdp_check.get('error')}",
                            user_action_hint=(
                                "Start Chrome with remote debugging enabled on your MacBook:\n"
                                f"  {get_macos_cdp_launch_command(9222, str(self.config.resolved_user_data_dir))}\n"
                                f"Then resume with: `gemini-bridge resume {task.task_id}`"
                            ),
                        )
                    browser = p.chromium.connect_over_cdp(self.config.cdp_url)
                    contexts = browser.contexts
                    context = contexts[0] if contexts else browser.new_context()
                else:
                    profile_dir = self.config.resolved_user_data_dir
                    profile_dir.mkdir(parents=True, exist_ok=True)
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=str(profile_dir),
                        headless=self.config.headless,
                    )
                    close_browser_when_done = True

                page = context.new_page()
                target_url = task.external_url if (not is_new_submission and task.external_url) else self.config.gemini_url
                if on_progress:
                    on_progress("navigating", {"url": target_url})
                page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2500)

                # 1. Check for CAPTCHA / MFA / Security challenge
                for sel in SELECTORS.security_challenge_indicators:
                    try:
                        if page.locator(sel).first.is_visible(timeout=500):
                            return BackendResult(
                                status=TaskStatus.NEEDS_USER_ACTION,
                                detection_state=DetectionState.VERIFIED,
                                external_url=page.url,
                                error_message="Security challenge, CAPTCHA, MFA, or manual takeover prompt detected.",
                                user_action_hint=(
                                    "Please complete the verification or sign-in challenge manually in the open browser window, "
                                    f"then run `gemini-bridge resume {task.task_id}`."
                                ),
                            )
                    except Exception:
                        continue

                # 2. Check Login State
                is_signed_in = False
                for sel in SELECTORS.authenticated_indicators:
                    try:
                        if page.locator(sel).first.is_visible(timeout=1000):
                            is_signed_in = True
                            break
                    except Exception:
                        continue

                if not is_signed_in:
                    for sel in SELECTORS.sign_in_buttons:
                        try:
                            if page.locator(sel).first.is_visible(timeout=500):
                                return BackendResult(
                                    status=TaskStatus.NEEDS_USER_ACTION,
                                    detection_state=DetectionState.VERIFIED,
                                    external_url=page.url,
                                    error_message="Google account is not signed in to Gemini web app.",
                                    user_action_hint=(
                                        "Please sign in to https://gemini.google.com/app in the browser window "
                                        "(Gemini Bridge never asks for your password or cookies), "
                                        f"then run `gemini-bridge resume {task.task_id}`."
                                    ),
                                )
                        except Exception:
                            continue

                # 3. Handle new submission if needed
                if is_new_submission:
                    if task.mode == TaskMode.APP_TASK.value:
                        # Attempt to locate the "Task" tab/button above the prompt box (Gemini App Tasks / Spark)
                        task_mode_activated = False
                        for sel in SELECTORS.app_task_tab_or_option:
                            try:
                                loc = page.locator(sel).first
                                if loc.is_visible(timeout=1200):
                                    loc.click()
                                    page.wait_for_timeout(1000)
                                    task_mode_activated = True
                                    break
                            except Exception:
                                continue

                        if not task_mode_activated:
                            return BackendResult(
                                status=TaskStatus.NEEDS_USER_ACTION,
                                detection_state=DetectionState.VERIFIED,
                                external_url=page.url,
                                error_message=(
                                    "Gemini App Tasks ('Task' mode / Scheduled Actions) UI entry point was not found on this account."
                                ),
                                user_action_hint=(
                                    "Per official Google documentation (https://support.google.com/gemini/answer/17094507), "
                                    "Tasks in Gemini Apps require:\n"
                                    "  1. A personal Google Account (not available on Workspace work/school accounts)\n"
                                    "  2. Google AI Pro or Ultra subscription\n"
                                    "  3. Keep Activity & Personal Intelligence enabled\n"
                                    "Alternatively, for Scheduled Actions, visit Settings & help -> Scheduled actions (up to 10 active schedules)."
                                ),
                            )

                    elif task.mode == TaskMode.DEEP_RESEARCH.value:
                        # Select Deep Research mode if available in the mode picker or toolbar
                        self._ensure_deep_research_selected(page)

                    # Locate prompt input box
                    input_box = None
                    for sel in SELECTORS.prompt_inputs:
                        try:
                            loc = page.locator(sel).first
                            if loc.is_visible(timeout=1500):
                                input_box = loc
                                break
                        except Exception:
                            continue

                    if input_box is None:
                        return BackendResult(
                            status=TaskStatus.FAILED,
                            detection_state=DetectionState.DOM_CHANGED_OR_UNPARSEABLE,
                            external_url=page.url,
                            error_message="Could not locate the Gemini prompt input box on the page (DOM structure may have changed).",
                            user_action_hint="Verify that https://gemini.google.com/app loaded properly in the browser.",
                        )

                    input_box.click()
                    input_box.fill(task.prompt)
                    page.wait_for_timeout(600)

                    # Submit prompt
                    submitted = False
                    for sel in SELECTORS.submit_buttons:
                        try:
                            btn = page.locator(sel).first
                            if btn.is_visible(timeout=1000) and btn.is_enabled():
                                btn.click()
                                submitted = True
                                break
                        except Exception:
                            continue
                    if not submitted:
                        input_box.press("Enter")

                    page.wait_for_timeout(3000)
                    if on_progress:
                        on_progress("submitted", {"external_url": page.url})

                # 4. Poll for plan confirmation & task completion
                return self._poll_page_until_complete(page, task, on_progress)

            finally:
                if close_browser_when_done and context is not None:
                    try:
                        context.close()
                    except Exception:
                        pass

    def _ensure_deep_research_selected(self, page: Any) -> bool:
        """Select Deep Research from the Gemini toolbar or model dropdown if present."""
        for sel in SELECTORS.deep_research_option:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=800):
                    loc.click()
                    page.wait_for_timeout(800)
                    return True
            except Exception:
                continue

        for trigger in SELECTORS.model_picker_triggers:
            try:
                btn = page.locator(trigger).first
                if btn.is_visible(timeout=800):
                    btn.click()
                    page.wait_for_timeout(800)
                    for opt in SELECTORS.deep_research_option:
                        opt_loc = page.locator(opt).first
                        if opt_loc.is_visible(timeout=800):
                            opt_loc.click()
                            page.wait_for_timeout(800)
                            return True
            except Exception:
                continue
        return False

    def _poll_page_until_complete(
        self,
        page: Any,
        task: TaskRecord,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> BackendResult:
        """Poll the Gemini conversation thread, auto-confirming the Deep Research plan if enabled,
        and distinguishing true completion from transient DOM stillness.
        """
        start_ts = time.monotonic()
        timeout_sec = max(30, self.config.task_timeout_seconds)
        poll_sec = max(2, self.config.poll_interval_seconds)
        required_stable = max(1, self.config.stability_checks)

        plan_confirmed = False
        last_length = 0
        stable_count = 0

        while (time.monotonic() - start_ts) < timeout_sec:
            current_url = page.url

            # Check if Deep Research generated a research plan waiting for "Start research" / "开始研究"
            if task.mode == TaskMode.DEEP_RESEARCH.value and not plan_confirmed:
                for sel in SELECTORS.start_research_buttons:
                    try:
                        btn = page.locator(sel).first
                        if btn.is_visible(timeout=500) and btn.is_enabled():
                            if self.config.auto_confirm_plan:
                                btn.click()
                                plan_confirmed = True
                                if on_progress:
                                    on_progress("plan_confirmed", {"external_url": current_url})
                                page.wait_for_timeout(2000)
                                break
                            else:
                                return BackendResult(
                                    status=TaskStatus.NEEDS_USER_ACTION,
                                    detection_state=DetectionState.VERIFIED,
                                    external_url=current_url,
                                    error_message="Deep Research generated a research plan and is waiting for user approval.",
                                    user_action_hint=(
                                        f"Review and click 'Start research' in the browser ({current_url}), "
                                        f"then run `gemini-bridge resume {task.task_id}`."
                                    ),
                                )
                    except Exception:
                        continue

            # Check if any active running spinner / stop button is visible
            is_actively_running = False
            for sel in SELECTORS.running_indicators:
                try:
                    if page.locator(sel).first.is_visible(timeout=400):
                        is_actively_running = True
                        break
                except Exception:
                    continue

            # Extract current report & sources snapshot
            snapshot = page.evaluate(JS_EXTRACT_REPORT_AND_SOURCES)
            raw_len = int(snapshot.get("rawLength") or 0)

            has_completion_marker = False
            for sel in SELECTORS.completion_indicators:
                try:
                    if page.locator(sel).first.is_visible(timeout=400):
                        has_completion_marker = True
                        break
                except Exception:
                    continue

            if not is_actively_running and has_completion_marker and raw_len > 120:
                if raw_len == last_length:
                    stable_count += 1
                else:
                    stable_count = 1
                    last_length = raw_len

                if stable_count >= required_stable:
                    return BackendResult(
                        status=TaskStatus.COMPLETED,
                        detection_state=DetectionState.VERIFIED,
                        report_markdown=snapshot.get("markdown", ""),
                        sources=snapshot.get("sources", []),
                        external_url=current_url,
                        extra={"raw_length": raw_len, "plan_confirmed": plan_confirmed},
                    )
            else:
                stable_count = 0
                last_length = raw_len

            if on_progress:
                on_progress(
                    "polling",
                    {
                        "external_url": current_url,
                        "is_running": is_actively_running,
                        "captured_chars": raw_len,
                    },
                )
            time.sleep(poll_sec)

        return BackendResult(
            status=TaskStatus.NEEDS_USER_ACTION,
            detection_state=DetectionState.UNKNOWN,
            external_url=page.url,
            error_message=f"Timed out after {timeout_sec}s waiting for web task completion.",
            user_action_hint=f"The task may still be running at {page.url}. Use `gemini-bridge fetch {task.task_id}` or `resume` later.",
        )
