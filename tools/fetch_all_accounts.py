import asyncio
import json
from playwright.async_api import async_playwright

async def extract_from_page(page):
    # Wait a bit for DOM
    await asyncio.sleep(2)
    data = await page.evaluate("""async () => {
        // Account info
        let accountInfo = "";
        const accountEl = document.querySelector('a[aria-label*="Google Account"], a[aria-label*="Google 帐号"], a[aria-label*="Google 帳號"], a[aria-label*="@gmail.com"], button[aria-label*="@gmail.com"]');
        if (accountEl) accountInfo = accountEl.getAttribute('aria-label') || "";

        // Check if sidebar has expand button and click if collapsed
        const menuBtn = document.querySelector('button[aria-label*="Main menu"], button[aria-label*="主菜单"], button[data-test-id="side-nav-menu-button"]');
        
        // Find scrollable container for sidebar
        const scrollContainers = [
            document.querySelector('mat-sidenav'),
            document.querySelector('side-navigation-v2'),
            document.querySelector('.conversation-container'),
            document.querySelector('[data-test-id="side-nav-container"]'),
            document.querySelector('nav')
        ].filter(Boolean);

        for (const sc of scrollContainers) {
            try {
                sc.scrollTop = 0;
                await new Promise(r => setTimeout(r, 200));
                for (let i = 0; i < 10; i++) {
                    sc.scrollTop += 600;
                    await new Promise(r => setTimeout(r, 200));
                }
            } catch(e) {}
        }

        // Collect all conversation links
        const items = [];
        const seen = new Set();
        
        // Select all anchor tags
        const allAnchors = Array.from(document.querySelectorAll('a'));
        for (const a of allAnchors) {
            const href = a.getAttribute('href') || '';
            const text = (a.innerText || '').trim();
            const title = a.getAttribute('title') || '';
            const aria = a.getAttribute('aria-label') || '';
            
            // Conversation links look like /app/<hash> or /u/<num>/app/<hash>
            if (href.match(/\\/app\\/[a-f0-9]{12,}/) || href.match(/\\/u\\/\\d+\\/app\\/[a-f0-9]{12,}/)) {
                if (!seen.has(href)) {
                    seen.add(href);
                    // Extract clean title
                    let display = title || text.split('\\n')[0] || aria;
                    items.push({
                        title: display,
                        href: href,
                        full_text: text.slice(0, 150)
                    });
                }
            }
        }

        // Collect Gems / Notebooks
        const gems = [];
        for (const a of allAnchors) {
            const href = a.getAttribute('href') || '';
            const text = (a.innerText || '').trim();
            if (href.includes('/gem/') || href.includes('notebooklm') || a.closest('[data-test-id*="gem"]')) {
                if (text && !seen.has(href + text)) {
                    seen.add(href + text);
                    gems.push({ text: text.split('\\n')[0], href });
                }
            }
        }

        return {
            pageTitle: document.title,
            currentUrl: window.location.href,
            accountInfo,
            conversations: items,
            gems
        };
    }""")
    return data

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        
        accounts = {
            0: {"target_prefix": "/app", "email": "whvemini@gmail.com", "page": None},
            1: {"target_prefix": "/u/1/app", "email": "healwoaini@gmail.com", "page": None},
            2: {"target_prefix": "/u/2/app", "email": "healwve@gmail.com", "page": None},
        }

        # Find existing pages or assign them
        for page in context.pages:
            url = page.url
            if "/u/2/app" in url and not accounts[2]["page"]:
                accounts[2]["page"] = page
            elif "/u/1/app" in url and not accounts[1]["page"]:
                accounts[1]["page"] = page
            elif ("gemini.google.com/app" in url or "/u/0/app" in url) and not accounts[0]["page"]:
                if "/u/1/" not in url and "/u/2/" not in url:
                    accounts[0]["page"] = page

        # If any missing, navigate an unused tab or new tab
        for acc_id, acc_data in accounts.items():
            if not acc_data["page"]:
                acc_data["page"] = await context.new_page()
                target_url = f"https://gemini.google.com{acc_data['target_prefix']}"
                await acc_data["page"].goto(target_url, wait_until="domcontentloaded", timeout=15000)

        results = []
        for acc_id, acc_data in accounts.items():
            page = acc_data["page"]
            print(f"\n==========================================")
            print(f"Extracting Account {acc_id} ({acc_data['email']}) from page: {page.url}")
            data = await extract_from_page(page)
            data["account_index"] = acc_id
            data["configured_email"] = acc_data["email"]
            results.append(data)
            print(f"Title: {data['pageTitle']}")
            print(f"Account: {data['accountInfo']}")
            print(f"Found {len(data['conversations'])} conversations and {len(data['gems'])} gems/notebooks")

        with open("tools/gemini_all_accounts_projects.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    asyncio.run(main())
