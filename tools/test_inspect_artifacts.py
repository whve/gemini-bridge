import asyncio
import json
from playwright.async_api import async_playwright

async def inspect_artifacts():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = [pg for pg in context.pages if "6b529e0491802c46" in pg.url][0]

        details = await page.evaluate(r"""() => {
            const btns = Array.from(document.querySelectorAll('model-response button'));
            return btns.map(b => {
                const card = b.closest('[data-test-id], mat-card, .card, div') || b.parentElement;
                return {
                    aria: b.getAttribute('aria-label') || '',
                    text: b.innerText,
                    cardText: card ? card.innerText.replace(/\n+/g, ' | ').slice(0, 200) : '',
                    cardTag: card ? card.tagName : '',
                    parentHtml: b.parentElement ? b.parentElement.outerHTML.slice(0, 300) : ''
                };
            });
        }""")
        
        for d in details:
            print("---")
            print("Aria:", d["aria"])
            print("Card text:", d["cardText"])
            print("Parent:", d["parentHtml"])

asyncio.run(inspect_artifacts())
