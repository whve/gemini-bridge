import asyncio
import json
from playwright.async_api import async_playwright

async def expand_and_get_conversations(page):
    try:
        # Check if sidebar menu button needs to be clicked
        await page.evaluate("""() => {
            const menuBtn = document.querySelector('button[aria-label*="Main menu"], button[aria-label*="主菜单"], button[data-test-id="side-nav-menu-button"], button[aria-label*="Expand menu"]');
            if (menuBtn && !document.querySelector('conversations-list')) {
                menuBtn.click();
            }
        }""")
        await asyncio.sleep(1)

        # Scroll sidebar to load all items
        for _ in range(5):
            await page.evaluate("""() => {
                const scrollable = document.querySelector('side-navigation-v2, mat-sidenav, .conversation-container, [data-test-id="conversation-list"]');
                if (scrollable) {
                    scrollable.scrollTop += 1000;
                }
            }""")
            await asyncio.sleep(0.5)

        data = await page.evaluate("""() => {
            let accountInfo = "";
            const accountEl = document.querySelector('a[aria-label*="Google Account"], a[aria-label*="Google 帐号"], a[aria-label*="Google 帳號"], a[aria-label*="@gmail.com"], button[aria-label*="@gmail.com"]');
            if (accountEl) accountInfo = accountEl.getAttribute('aria-label') || "";

            const items = [];
            const seen = new Set();
            
            // All links with conversation URLs
            const links = document.querySelectorAll('a');
            for (const a of links) {
                const href = a.getAttribute('href') || '';
                const text = (a.innerText || '').trim();
                const title = a.getAttribute('title') || '';
                const aria = a.getAttribute('aria-label') || '';
                
                // Matches /app/<id> or /u/<n>/app/<id>
                if (href.match(/\/app\/[a-f0-9]{10,}/)) {
                    if (!seen.has(href)) {
                        seen.add(href);
                        // Extract conversation title
                        // usually text is "title\n..." or just title
                        let raw = title || text || aria;
                        let cleanTitle = raw.split('\\n')[0].trim();
                        // avoid empty or just "more"
                        if (cleanTitle && cleanTitle !== "More" && cleanTitle !== "更多") {
                            items.push({
                                title: cleanTitle,
                                full_preview: text.replace(/\\s+/g, ' ').slice(0, 150),
                                href: href
                            });
                        }
                    }
                }
            }

            // NotebookLM / Gems / Custom
            const gems = [];
            for (const a of links) {
                const href = a.getAttribute('href') || '';
                const text = (a.innerText || '').trim();
                const title = a.getAttribute('title') || '';
                if (href.includes('/gem/') || href.includes('notebooklm.google.com') || a.closest('gems-list, [data-test-id*="gem"]')) {
                    const name = title || text.split('\\n')[0].trim();
                    if (name && !seen.has(href + name)) {
                        seen.add(href + name);
                        gems.push({ title: name, href });
                    }
                }
            }

            return {
                url: window.location.href,
                pageTitle: document.title,
                accountInfo,
                items,
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
        
        # Pick the best tab for each account:
        # Account 0: url has /app/ and not /u/1/ and not /u/2/
        # Account 1: url has /u/1/app
        # Account 2: url has /u/2/app
        
        pages_by_acc = {0: [], 1: [], 2: []}
        for page in context.pages:
            url = page.url
            if "/u/2/app" in url:
                pages_by_acc[2].append(page)
            elif "/u/1/app" in url:
                pages_by_acc[1].append(page)
            elif "gemini.google.com/app" in url:
                pages_by_acc[0].append(page)
                
        print(f"Found tabs: Acc 0: {len(pages_by_acc[0])}, Acc 1: {len(pages_by_acc[1])}, Acc 2: {len(pages_by_acc[2])}")
        
        results = {}
        for acc_id, pages in pages_by_acc.items():
            results[acc_id] = []
            # Check all pages for this account and combine
            all_items = {}
            all_gems = {}
            acc_name = ""
            for pg in pages:
                data = await expand_and_get_conversations(pg)
                if data.get("accountInfo"):
                    acc_name = data["accountInfo"]
                for item in data.get("items", []):
                    all_items[item["href"]] = item
                for gem in data.get("gems", []):
                    all_gems[gem["title"]] = gem
                    
            results[acc_id] = {
                "account_id": acc_id,
                "account_name": acc_name,
                "conversations": list(all_items.values()),
                "gems": list(all_gems.values())
            }

        with open("tools/gemini_extracted_accounts.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        print("\nExtraction summary:")
        for acc_id, data in results.items():
            print(f"\n================ ACCOUNT {acc_id} ================")
            print(f"Identity: {data['account_name']}")
            print(f"Conversations ({len(data['conversations'])}):")
            for c in data["conversations"]:
                print(f"  - {c['title']} ({c['href']})")
            print(f"Gems/Notebooks ({len(data['gems'])}):")
            for g in data["gems"]:
                print(f"  - {g['title']} ({g['href']})")

if __name__ == "__main__":
    asyncio.run(main())
