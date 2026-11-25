import asyncio
from playwright.async_api import async_playwright

async def test_click():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = [pg for pg in context.pages if "6b529e0491802c46" in pg.url][0]

        btn = page.locator('button[aria-label="Open report.qmd"]').first
        print("Clicking Open report.qmd...")
        await btn.click()
        await page.wait_for_timeout(2000)

        # Inspect what is newly visible
        info = await page.evaluate(r"""() => {
            const driveViewer = document.querySelector('.drive-viewer, [class*="drive-viewer"], [class*="viewer"], mat-dialog-container');
            const allBtns = Array.from(document.querySelectorAll('button'));
            const downloadBtns = allBtns.filter(b => {
                const a = (b.getAttribute('aria-label') || '').toLowerCase();
                const t = (b.innerText || '').toLowerCase();
                return a.includes('download') || a.includes('下载') || t.includes('download') || t.includes('下载');
            }).map(b => ({
                aria: b.getAttribute('aria-label') || '',
                text: b.innerText,
                className: b.className
            }));
            const modals = Array.from(document.querySelectorAll('[role="dialog"], mat-dialog-container, .modal')).map(m => m.className);
            return {
                hasViewer: !!driveViewer,
                viewerClass: driveViewer ? driveViewer.className : null,
                downloadBtns,
                modals
            };
        }""")
        print("After click info:", info)
        
        # Press Escape to close modal
        await page.keyboard.press("Escape")

asyncio.run(test_click())
