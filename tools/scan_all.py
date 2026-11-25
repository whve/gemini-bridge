import asyncio
import json
from playwright.async_api import async_playwright

async def scan_all_tabs():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        
        accounts_data = {
            "u0": {"name": "Account 0 (whvemini@gmail.com)", "email": "whvemini@gmail.com", "convs": {}, "gems": {}},
            "u1": {"name": "Account 1 (healwoaini@gmail.com)", "email": "healwoaini@gmail.com", "convs": {}, "gems": {}},
            "u2": {"name": "Account 2 (healwve@gmail.com)", "email": "healwve@gmail.com", "convs": {}, "gems": {}},
        }
        
        for idx, page in enumerate(context.pages):
            url = page.url
            if "gemini.google.com" not in url:
                continue
                
            try:
                # determine account from url
                acc_key = "u0"
                if "/u/1/" in url:
                    acc_key = "u1"
                elif "/u/2/" in url:
                    acc_key = "u2"

                # Check account from DOM profile
                dom_account = await page.evaluate("""() => {
                    const btn = document.querySelector('a[aria-label*="Google Account"], a[aria-label*="Google 帐号"], a[aria-label*="Google 帳號"], button[aria-label*="@gmail.com"], a[aria-label*="@gmail.com"]');
                    return btn ? btn.getAttribute('aria-label') : '';
                }""")
                if "healwoaini@gmail.com" in dom_account:
                    acc_key = "u1"
                elif "healwve@gmail.com" in dom_account:
                    acc_key = "u2"
                elif "whvemini@gmail.com" in dom_account:
                    acc_key = "u0"

                links_data = await page.evaluate("""() => {
                    const links = Array.from(document.querySelectorAll('a'));
                    return links.map(l => ({
                        href: l.href,
                        text: (l.innerText || '').trim(),
                        title: l.title || '',
                        aria: l.getAttribute('aria-label') || ''
                    }));
                }""")
                
                for item in links_data:
                    href = item["href"]
                    # Clean title
                    raw_title = item["title"] or item["text"].split('\n')[0] or item["aria"]
                    raw_title = raw_title.strip()
                    
                    if "/app/" in href and not href.endswith('/app') and not href.endswith('/app/'):
                        # Strip query params for unique key
                        clean_url = href.split('?')[0]
                        if raw_title and len(raw_title) > 0 and raw_title not in ["Sign in", "登录", "New chat", "新对话"]:
                            accounts_data[acc_key]["convs"][clean_url] = {
                                "title": raw_title,
                                "url": href,
                                "raw_text": item["text"][:100]
                            }
                    
                    if "notebooklm.google.com" in href or "/gem/" in href:
                        if raw_title and "notebook" not in raw_title.lower() or len(raw_title) > 3:
                            accounts_data[acc_key]["gems"][href] = {
                                "title": raw_title,
                                "url": href
                            }
                            
                print(f"Tab {idx} ({url}): classified as {acc_key}, total convs so far: {len(accounts_data[acc_key]['convs'])}")

            except Exception as e:
                print(f"Tab {idx} error: {e}")

        # Let's save the result
        with open("tools/gemini_extracted_accounts.json", "w", encoding="utf-8") as f:
            json.dump(accounts_data, f, ensure_ascii=False, indent=2)

asyncio.run(scan_all_tabs())
