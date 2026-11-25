"""DOM Adapter Layer and Browser Session Management for Gemini Web UI.

Isolates all CSS/XPath/ARIA selectors and DOM inspection heuristics so that if
Google updates `gemini.google.com`, only this adapter needs adjustment without
crashing the task engine.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx


@dataclass
class GeminiSelectors:
    """Resilient multi-fallback selector registry for gemini.google.com."""

    # Authentication / Login detection
    sign_in_buttons: List[str] = field(
        default_factory=lambda: [
            "a[href*='accounts.google.com/ServiceLogin']",
            "button:has-text('Sign in')",
            "a:has-text('Sign in')",
            "button:has-text('登录')",
            "a:has-text('登录')",
        ]
    )
    authenticated_indicators: List[str] = field(
        default_factory=lambda: [
            "rich-textarea",
            "div[contenteditable='true'][role='textbox']",
            "textarea[aria-label*='prompt' i]",
            "a[aria-label*='Google Account' i]",
            "img[alt*='Profile' i]",
        ]
    )

    # CAPTCHA / MFA / Anti-automation / Takeover prompts
    security_challenge_indicators: List[str] = field(
        default_factory=lambda: [
            "iframe[src*='recaptcha']",
            "div#captcha-form",
            "input[name='totpPin']",
            "text='Verify it\\'s you'",
            "text=' Unusual traffic '",
            "button:has-text('Take over')",
            "button:has-text('接管')",
        ]
    )

    # Mode selection (Deep Research / Task)
    model_picker_triggers: List[str] = field(
        default_factory=lambda: [
            "button[data-test-id='bard-mode-menu-button']",
            "button[aria-label*='model' i]",
            "button:has-text('Deep Research')",
            "button:has-text('Flash')",
            "button:has-text('Pro')",
        ]
    )
    deep_research_option: List[str] = field(
        default_factory=lambda: [
            "[role='menuitem']:has-text('Deep Research')",
            "[role='option']:has-text('Deep Research')",
            "button:has-text('Deep Research')",
            "span:has-text('Deep Research')",
            "[data-test-id*='deep-research' i]",
        ]
    )
    app_task_tab_or_option: List[str] = field(
        default_factory=lambda: [
            "button:has-text('Task')",
            "[role='tab']:has-text('Task')",
            "button[aria-label='Task']",
            "[data-test-id*='task-mode' i]",
            "a[href*='/scheduled']",
        ]
    )

    # Prompt input and submission
    prompt_inputs: List[str] = field(
        default_factory=lambda: [
            "rich-textarea div[contenteditable='true']",
            "div[contenteditable='true'][role='textbox']",
            "textarea",
        ]
    )
    submit_buttons: List[str] = field(
        default_factory=lambda: [
            "button[aria-label*='Send message' i]",
            "button[aria-label*='Submit' i]",
            "button[aria-label*='发送' i]",
            "button.send-button",
            "button[data-test-id='send-button']",
        ]
    )

    # Deep Research two-step plan confirmation ("Start research" / "开始研究")
    start_research_buttons: List[str] = field(
        default_factory=lambda: [
            "button:has-text('Start research')",
            "button:has-text('开始研究')",
            "button:has-text('Confirm')",
            "button[aria-label*='Start research' i]",
            "[data-test-id='start-research-button']",
        ]
    )

    # Active progress / streaming indicators (if present, task is still running!)
    running_indicators: List[str] = field(
        default_factory=lambda: [
            "button[aria-label*='Stop' i]",
            "button[aria-label*='停止' i]",
            "mat-progress-bar",
            "mat-spinner",
            "[data-test-id='research-progress']",
            "div:has-text('Researching websites')",
            "div:has-text('Analyzing results')",
            "div:has-text('正在研究')",
        ]
    )

    # Explicit completion markers for Deep Research report & Canvas
    completion_indicators: List[str] = field(
        default_factory=lambda: [
            "button:has-text('Export to Docs')",
            "button:has-text('导出到 Google 文档')",
            "button[aria-label*='Export' i]",
            "button[aria-label*='Share & export' i]",
            "button[aria-label*='Copy' i]",
            "deep-research-report",
            "message-content",
        ]
    )

    # Report content containers
    report_containers: List[str] = field(
        default_factory=lambda: [
            "deep-research-report",
            "immersive-editor",
            "canvas-panel",
            "model-response message-content",
            "div.markdown-main-panel",
            "article",
        ]
    )


SELECTORS = GeminiSelectors()


# JavaScript snippet injected into the page to extract clean Markdown and verified links
JS_EXTRACT_REPORT_AND_SOURCES = """
() => {
    const containerSelectors = [
        'deep-research-report',
        'immersive-editor',
        'canvas-panel',
        'model-response:last-of-type message-content',
        'message-content:last-of-type',
        'div.markdown-main-panel',
        'article',
        'main'
    ];
    let target = null;
    for (const sel of containerSelectors) {
        const nodes = document.querySelectorAll(sel);
        if (nodes && nodes.length > 0) {
            target = nodes[nodes.length - 1];
            if (target.innerText && target.innerText.trim().length > 80) {
                break;
            }
        }
    }
    if (!target) {
        return { found: false, markdown: '', sources: [], rawLength: 0 };
    }

    const sources = [];
    const seenUrls = new Set();
    const anchors = target.querySelectorAll('a[href]');
    anchors.forEach((a) => {
        const href = a.href || a.getAttribute('href') || '';
        if (href.startsWith('http://') || href.startsWith('https://')) {
            if (!href.includes('accounts.google.com') && !seenUrls.has(href)) {
                seenUrls.add(href);
                sources.push({
                    title: (a.innerText || a.getAttribute('aria-label') || a.title || href).trim(),
                    url: href
                });
            }
        }
    });

    // Also inspect citation chips / source cards on the page
    const sourceCards = document.querySelectorAll('a[data-test-id*="source"], a.source-card, div.sources-list a[href]');
    sourceCards.forEach((a) => {
        const href = a.href || '';
        if ((href.startsWith('http://') || href.startsWith('https://')) && !seenUrls.has(href)) {
            seenUrls.add(href);
            sources.push({
                title: (a.innerText || a.title || href).trim(),
                url: href
            });
        }
    });

    // Convert DOM structure into clean Markdown preserving headings, lists, tables, KaTeX, and inline links
    function nodeToMd(node) {
        if (node.nodeType === Node.TEXT_NODE) {
            return node.textContent || '';
        }
        if (node.nodeType !== Node.ELEMENT_NODE) {
            return '';
        }
        const tag = node.tagName.toLowerCase();

        // Handle KaTeX formulas cleanly (avoiding duplicate mathml + html text duplication)
        if (node.classList && node.classList.contains('katex')) {
            const annotation = node.querySelector('annotation[encoding="application/x-tex"], annotation');
            if (annotation) {
                const tex = annotation.textContent.trim();
                const isDisplay = node.classList.contains('katex-display') || Boolean(node.closest('.katex-display'));
                return isDisplay ? ('\\n$$\\n' + tex + '\\n$$\\n') : ('$' + tex + '$');
            }
            const htmlPart = node.querySelector('.katex-html');
            if (htmlPart) {
                return '$' + (htmlPart.innerText || '').trim() + '$';
            }
        }
        if (node.classList && (node.classList.contains('katex-mathml') || node.classList.contains('katex-html'))) {
            return '';
        }

        // Ignore UI controls, SVGs, buttons
        if (['script', 'style', 'noscript', 'svg', 'button', 'mat-icon'].includes(tag)) {
            return '';
        }

        // Handle Markdown tables
        if (tag === 'table') {
            const rows = Array.from(node.querySelectorAll('tr'));
            if (rows.length === 0) return '';
            let out = '\\n';
            let isFirst = true;
            for (const r of rows) {
                const cells = Array.from(r.querySelectorAll('th, td')).map(c => nodeToMd(c).trim().replace(/\\n+/g, ' '));
                out += '| ' + cells.join(' | ') + ' |\\n';
                if (isFirst) {
                    out += '| ' + cells.map(() => '---').join(' | ') + ' |\\n';
                    isFirst = false;
                }
            }
            return out + '\\n';
        }

        let childrenText = '';
        for (const child of node.childNodes) {
            childrenText += nodeToMd(child);
        }
        if (tag === 'h1') return '\\n# ' + childrenText.trim() + '\\n\\n';
        if (tag === 'h2') return '\\n## ' + childrenText.trim() + '\\n\\n';
        if (tag === 'h3') return '\\n### ' + childrenText.trim() + '\\n\\n';
        if (tag === 'h4') return '\\n#### ' + childrenText.trim() + '\\n\\n';
        if (tag === 'p') return childrenText.trim() + '\\n\\n';
        if (tag === 'li') return '- ' + childrenText.trim() + '\\n';
        if (tag === 'ul' || tag === 'ol') return '\\n' + childrenText + '\\n';
        if (tag === 'strong' || tag === 'b') return '**' + childrenText.trim() + '**';
        if (tag === 'em' || tag === 'i') return '*' + childrenText.trim() + '*';
        if (tag === 'code') return '`' + childrenText + '`';
        if (tag === 'pre') return '\\n```\\n' + (node.innerText || childrenText).trim() + '\\n```\\n\\n';
        if (tag === 'blockquote') return '\\n> ' + childrenText.trim().replace(/\\n/g, '\\n> ') + '\\n\\n';
        if (tag === 'a') {
            const href = node.href || node.getAttribute('href') || '';
            const label = childrenText.trim() || href;
            if (href.startsWith('http://') || href.startsWith('https://')) {
                return '[' + label + '](' + href + ')';
            }
            return label;
        }
        return childrenText;
    }

    const md = nodeToMd(target).replace(/\\n{3,}/g, '\\n\\n').trim();
    const fallbackText = (target.innerText || '').trim();
    const finalMd = md.length >= fallbackText.length * 0.5 ? md : fallbackText;

    return {
        found: finalMd.length > 0,
        markdown: finalMd,
        sources: sources,
        rawLength: finalMd.length
    };
}
"""


def probe_cdp_endpoint(cdp_url: str, timeout: float = 2.0) -> Dict[str, Any]:
    """Check whether a local Chrome/Chromium DevTools Protocol endpoint is reachable."""
    version_url = cdp_url.rstrip("/") + "/json/version"
    try:
        resp = httpx.get(version_url, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "reachable": True,
                "cdp_url": cdp_url,
                "browser": data.get("Browser"),
                "webSocketDebuggerUrl": data.get("webSocketDebuggerUrl"),
            }
        return {"reachable": False, "cdp_url": cdp_url, "error": f"HTTP {resp.status_code}"}
    except Exception as exc:
        return {"reachable": False, "cdp_url": cdp_url, "error": str(exc)}


def detect_installed_browsers() -> Dict[str, Any]:
    """Detect available Chrome/Chromium browsers and automation libraries on macOS or Linux."""
    system = platform.system()
    candidates = []
    if system == "Darwin":
        mac_paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
        for p in mac_paths:
            if Path(p).exists():
                candidates.append(p)
    for binary in ("google-chrome", "chromium", "chromium-browser", "brave-browser"):
        found = shutil.which(binary)
        if found and found not in candidates:
            candidates.append(found)

    playwright_installed = False
    playwright_version = None
    try:
        import playwright  # type: ignore

        playwright_installed = True
        playwright_version = getattr(playwright, "__version__", "installed")
    except ImportError:
        pass

    selenium_installed = False
    selenium_version = None
    try:
        import selenium  # type: ignore

        selenium_installed = True
        selenium_version = getattr(selenium, "__version__", "installed")
    except ImportError:
        pass

    return {
        "os": system,
        "os_release": platform.release(),
        "browsers_found": candidates,
        "playwright_installed": playwright_installed,
        "playwright_version": playwright_version,
        "selenium_installed": selenium_installed,
        "selenium_version": selenium_version,
    }


def get_macos_cdp_launch_command(port: int = 9222, profile_dir: str = "~/.gemini-bridge/chrome-profile") -> str:
    """Return the safe macOS command to launch Chrome with a dedicated persistent profile and CDP enabled."""
    return (
        f'/Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome '
        f'--remote-debugging-port={port} '
        f'--user-data-dir="{profile_dir}" '
        f'"https://gemini.google.com/app"'
    )
