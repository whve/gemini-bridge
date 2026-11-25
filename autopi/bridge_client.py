"""Gemini Task Delegation & Artifact Downloader Client (via Chrome CDP or Mock).

Directs complex multi-step synthesis, code generation, and deep research to the
logged-in Gemini web session, waits for completion, and downloads all artifacts locally.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autopi.bridge")


class GeminiBridgeClient:
    def __init__(
        self,
        cdp_url: str = "http://127.0.0.1:9222",
        mock_mode: bool = False,
    ) -> None:
        self.cdp_url = cdp_url
        self.mock_mode = mock_mode

    def check_health(self) -> Dict[str, Any]:
        if self.mock_mode:
            return {"connected": True, "mode": "mock", "endpoint": "local://mock"}
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(self.cdp_url, timeout=3000)
                context = browser.contexts[0]
                pages = [pg.url for pg in context.pages]
                return {
                    "connected": True,
                    "mode": "live_cdp",
                    "endpoint": self.cdp_url,
                    "open_tabs": len(pages),
                    "gemini_tabs": sum(1 for u in pages if "gemini.google.com" in u),
                }
        except Exception as exc:
            return {"connected": False, "mode": "offline", "error": str(exc)}

    def execute_delegation(
        self,
        prompt: str,
        account_url: Optional[str] = None,
        download_dir: Path | str = "./downloads",
        wait_timeout_sec: float = 300.0,
    ) -> Dict[str, Any]:
        """Submits task prompt to Gemini Task, waits, and collects all downloadable deliverables."""
        target_account_url = account_url or os.environ.get("GEMINI_ACCOUNT_URL", "https://gemini.google.com/u/0/app?pageId=none")
        download_path = Path(download_dir).resolve()
        download_path.mkdir(parents=True, exist_ok=True)

        if self.mock_mode:
            return self._execute_mock(prompt, download_path)

        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(self.cdp_url, timeout=10000)
            context = browser.contexts[0]
            
            # Reuse existing clean tab or open a new one
            page = None
            for pg in context.pages:
                if "/u/0/app" in pg.url or ("gemini.google.com/app" in pg.url and "/u/1/" not in pg.url and "/u/2/" not in pg.url):
                    # Check if not currently running a task
                    stop_btn = pg.locator('button[aria-label*="Stop" i], button:has-text("Stop")').first
                    if not stop_btn.is_visible():
                        page = pg
                        break

            if page is None:
                page = context.new_page()

            logger.info("Opening Gemini session at %s", target_account_url)
            page.goto(target_account_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2000)

            # Ensure Task mode is activated
            task_tab = page.locator('button:has-text("Task"), [role="tab"]:has-text("Task")').first
            if task_tab.is_visible(timeout=1000):
                task_tab.click()
                page.wait_for_timeout(600)

            # Fill prompt
            box = page.locator('rich-textarea div[contenteditable="true"]').first
            box.click()
            box.fill(prompt)
            page.wait_for_timeout(500)

            # Send
            send_btn = page.locator('button[aria-label*="Send message" i], button.send-button, button[data-test-id="send-button"]').first
            send_btn.click()
            page.wait_for_timeout(4000)
            task_url = page.url

            # Wait for execution to finish
            start_t = time.time()
            stop_btn = page.locator('button[aria-label*="Stop" i], button:has-text("Stop")').first
            while stop_btn.is_visible() and (time.time() - start_t) < wait_timeout_sec:
                time.sleep(4)

            # Collect deliverables
            downloaded_files: List[str] = []
            
            # 1. Download generated files with Open buttons (Drive Viewer)
            open_btns = page.locator('button[aria-label*="Open " i]').all()
            for b in open_btns:
                try:
                    aria = b.get_attribute("aria-label") or ""
                    fname = aria.replace("Open ", "").strip()
                    if not fname:
                        continue
                    # Dismiss existing overlays
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
                    try:
                        b.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass
                    b.click(force=True, timeout=2000)
                    page.wait_for_timeout(1500)
                    
                    # Check for download button in drive-viewer
                    dl_btn = page.locator('.drive-viewer [aria-label="Download"], .drive-viewer-content-download-button-v2').first
                    if dl_btn.is_visible(timeout=1000):
                        try:
                            save_file = download_path / fname
                            with page.expect_download(timeout=15000) as dl_info:
                                dl_btn.click(timeout=2000)
                            download = dl_info.value
                            download.save_as(str(save_file))
                            downloaded_files.append(str(save_file))
                        except Exception as err:
                            logger.warning("Failed to download %s: %s", fname, err)
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(400)
                except Exception as b_err:
                    logger.debug("Skipping button: %s", b_err)

            # 2. Download direct artifact cards with Download code / Export
            export_btns = page.locator('response-element button[aria-label*="Export" i], response-element button:has-text("Export")').all()
            for eb in export_btns:
                try:
                    page.keyboard.press("Escape")
                    eb.scroll_into_view_if_needed()
                    eb.click(force=True)
                    page.wait_for_timeout(600)
                    dl_code = page.locator('button:has-text("Download code"), [role="menuitem"]:has-text("Download code")').first
                    if dl_code.is_visible():
                        with page.expect_download(timeout=15000) as dl_info:
                            dl_code.click()
                        download = dl_info.value
                        save_file = download_path / "index.html"
                        download.save_as(str(save_file))
                        downloaded_files.append(str(save_file))
                except Exception:
                    pass
                page.keyboard.press("Escape")

            return {
                "success": True,
                "task_url": task_url,
                "duration_sec": round(time.time() - start_t, 2),
                "downloaded_files": downloaded_files,
            }

    def _execute_mock(self, prompt: str, download_path: Path) -> Dict[str, Any]:
        """Deterministic mock execution for offline testing."""
        mock_file = download_path / "mock_deliverable.py"
        mock_file.write_text(
            '"""Mock tool deliverable generated by AutoPi."""\ndef add(a, b): return a + b\n'
            'def test_add(): assert add(2, 3) == 5\nif __name__ == "__main__": test_add(); print("OK")\n',
            encoding="utf-8",
        )
        return {
            "success": True,
            "task_url": "http://127.0.0.1:9222/mock_task_001",
            "duration_sec": 0.5,
            "downloaded_files": [str(mock_file)],
        }
