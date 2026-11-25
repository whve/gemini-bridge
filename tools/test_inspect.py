import asyncio
import json
import os
import sys
from pathlib import Path
from playwright.async_api import async_playwright

async def inspect():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = None
        for pg in context.pages:
            if "6b529e0491802c46" in pg.url:
                page = pg
                break
        
        if not page:
            print("Not open, creating page...")
            page = await context.new_page()
            await page.goto("https://gemini.google.com/u/1/app/6b529e0491802c46?pageId=none")
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(3)

        info = await page.evaluate(r"""() => {
            const queries = Array.from(document.querySelectorAll('user-query, .user-query-container')).map(el => el.innerText.trim());
            const responses = Array.from(document.querySelectorAll('model-response')).map(mr => {
                const text = mr.innerText;
                const buttons = Array.from(mr.querySelectorAll('button')).map(b => ({
                    aria: b.getAttribute('aria-label') || '',
                    text: b.innerText.trim()
                }));
                return {
                    textLength: text.length,
                    buttons
                };
            });
            
            const openBtns = Array.from(document.querySelectorAll('button')).filter(b => b.innerText.trim() === 'Open' || (b.getAttribute('aria-label')||'').includes('Open'));
            const openBtnDetails = openBtns.map(b => {
                const parent = b.parentElement ? b.parentElement.innerText : '';
                return {
                    aria: b.getAttribute('aria-label'),
                    text: b.innerText,
                    parentSnippet: parent.replace(/\n+/g, ' ').slice(0, 150),
                    tag: b.tagName
                };
            });

            return {
                title: document.title,
                queries,
                responses,
                openBtnDetails
            };
        }""")
        
        print("Title:", info["title"])
        print("Queries count:", len(info["queries"]))
        if info["queries"]:
            print("Query 0 preview:", info["queries"][0][:200])
        print("Responses count:", len(info["responses"]))
        for i, r in enumerate(info["responses"]):
            print(f"Response {i}: len={r['textLength']}, buttons={r['buttons']}")
        print("Open buttons:", len(info["openBtnDetails"]))
        for b in info["openBtnDetails"]:
            print(" - Button:", b)

asyncio.run(inspect())
