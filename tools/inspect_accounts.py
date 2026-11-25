import asyncio
import json
from playwright.async_api import async_playwright

async def get_account_data(page):
    try:
        data = await page.evaluate("""() => {
            // Find account info
            let accountInfo = "";
            const accountEl = document.querySelector('a[aria-label*="Google Account"], a[aria-label*="Google 帐号"], a[aria-label*="Google 帳號"], a[aria-label*="@gmail.com"]');
            if (accountEl) {
                accountInfo = accountEl.getAttribute('aria-label') || "";
            }
            if (!accountInfo) {
                // try avatar or profile button
                const btn = document.querySelector('button[aria-label*="@gmail.com"], button[aria-label*="Google Account"], button[aria-label*="Google 帐号"]');
                if (btn) accountInfo = btn.getAttribute('aria-label') || "";
            }

            // Find all sidebar conversation items
            const convs = [];
            // Gemini uses various elements for conversations
            const convElements = document.querySelectorAll('conversations-list a, [data-test-id="conversation-list-item"], nav a, a[href*="/app/"]');
            const seen = new Set();
            for (const el of convElements) {
                const href = el.getAttribute('href') || '';
                const text = (el.innerText || '').trim();
                const title = el.getAttribute('title') || '';
                const aria = el.getAttribute('aria-label') || '';
                const name = text || title || aria;
                if (name && !seen.has(href + name)) {
                    seen.add(href + name);
                    convs.push({ name: name.replace(/\\n/g, ' -- '), href, aria });
                }
            }

            // Also look for Gems or pinned items or custom instructions/projects
            const gemElements = document.querySelectorAll('a[href*="/gem/"], [data-test-id*="gem"]');
            const gems = [];
            for (const el of gemElements) {
                gems.push({ text: (el.innerText || '').trim().replace(/\\n/g, ' -- '), href: el.getAttribute('href') });
            }

            return {
                title: document.title,
                url: window.location.href,
                accountInfo,
                convs,
                gems
            };
        }""")
        return data
    except Exception as e:
        return {"error": str(e), "url": page.url}

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        print(f"Total open pages: {len(context.pages)}")
        
        # Let's inspect each unique gemini tab
        gemini_pages = [p for p in context.pages if "gemini.google.com" in p.url]
        print(f"Total Gemini pages: {len(gemini_pages)}")
        
        for idx, page in enumerate(gemini_pages):
            res = await get_account_data(page)
            print(f"\n--- Gemini Tab {idx}: {page.url} ---")
            print(f"Title: {res.get('title')}")
            print(f"Account: {res.get('accountInfo')}")
            convs = res.get('convs', [])
            print(f"Conversations found ({len(convs)}):")
            for c in convs[:15]:
                print(f"   * {c['name']} ({c['href']})")
            gems = res.get('gems', [])
            if gems:
                print(f"Gems found ({len(gems)}):")
                for g in gems:
                    print(f"   * {g['text']} ({g['href']})")

if __name__ == "__main__":
    asyncio.run(main())
