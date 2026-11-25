import asyncio
import json
from playwright.async_api import async_playwright

async def inspect_cards():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = [pg for pg in context.pages if "6b529e0491802c46" in pg.url][0]

        data = await page.evaluate(r"""() => {
            const btns = Array.from(document.querySelectorAll('model-response button'));
            return btns.map(b => {
                let p = b;
                for (let i = 0; i < 5; i++) {
                    if (p.parentElement) p = p.parentElement;
                }
                return {
                    aria: b.getAttribute('aria-label') || '',
                    text: b.innerText,
                    outerHTML: p.outerHTML.slice(0, 500)
                };
            }).filter(x => x.aria.startsWith('Open '));
        }""")
        for d in data:
            print("=====")
            print("ARIA:", d["aria"])
            print("HTML:", d["outerHTML"])

asyncio.run(inspect_cards())
