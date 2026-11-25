import asyncio
import os
import re
from pathlib import Path
from playwright.async_api import async_playwright

DOWNLOAD_DIR = Path("./downloads/6b529e0491802c46")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

async def download_all():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        
        # Locate the tab
        page = None
        for pg in context.pages:
            if "6b529e0491802c46" in pg.url:
                page = pg
                break
        
        if not page:
            page = await context.new_page()
            await page.goto("https://gemini.google.com/u/1/app/6b529e0491802c46?pageId=none")
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)

        print("Tab found:", await page.title())

        # 1. Extract markdown report & conversation
        conv_data = await page.evaluate(r"""() => {
            const queries = Array.from(document.querySelectorAll('user-query, .user-query-container')).map(el => el.innerText.trim());
            const responses = Array.from(document.querySelectorAll('model-response')).map(el => el.innerText.trim());
            
            // Extract links / sources
            const links = Array.from(document.querySelectorAll('model-response a[href]')).map(a => ({
                text: a.innerText.trim(),
                href: a.href
            })).filter(x => x.href && !x.href.includes('accounts.google.com'));

            return {
                title: document.title,
                queries,
                responses,
                links
            };
        }""")

        # Save conversation as Markdown
        report_file = DOWNLOAD_DIR / "conversation_and_report.md"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(f"# {conv_data['title']}\n\n")
            f.write(f"**Source URL**: https://gemini.google.com/u/1/app/6b529e0491802c46?pageId=none\n\n---\n\n")
            for i, q in enumerate(conv_data["queries"]):
                f.write(f"## User Prompt\n\n{q}\n\n---\n\n")
                if i < len(conv_data["responses"]):
                    f.write(f"## Model Output\n\n{conv_data['responses'][i]}\n\n---\n\n")

        print("Saved conversation & report to:", report_file)

        # Save sources
        sources_file = DOWNLOAD_DIR / "sources.md"
        with open(sources_file, "w", encoding="utf-8") as f:
            f.write("# References & Sources\n\n")
            seen = set()
            for l in conv_data["links"]:
                if l["href"] not in seen:
                    seen.add(l["href"])
                    f.write(f"- [{l['text'] or l['href']}]({l['href']})\n")
        print("Saved sources to:", sources_file)

        # 2. Download all generated files
        # The buttons are in generated-file
        files_info = await page.evaluate(r"""() => {
            return Array.from(document.querySelectorAll('generated-file')).map(f => {
                const btn = f.querySelector('button');
                return {
                    aria: btn ? btn.getAttribute('aria-label') : '',
                    text: f.innerText.replace(/\n+/g, ' ')
                };
            });
        }""")

        print(f"Found {len(files_info)} generated files:")
        for fi in files_info:
            print(" -", fi)

        for fi in files_info:
            aria = fi["aria"]
            if not aria:
                continue
            
            # Figure out filename
            # e.g., 'Open AI_Science_Arena_Task02_Research_Report' -> 'AI_Science_Arena_Task02_Research_Report'
            raw_name = aria.replace("Open ", "").strip()
            # Determine extension from fi['text'] if not present
            ext_map = {"PDF": ".pdf", "MD": ".md", "BIB": ".bib", "YML": ".yml", "ZIP": ".zip"}
            ext = ""
            for k, v in ext_map.items():
                if f" {k} " in fi["text"] or fi["text"].endswith(f" {k}"):
                    if not raw_name.endswith(v):
                        ext = v
                    break
            
            target_filename = raw_name + ext
            save_path = DOWNLOAD_DIR / target_filename
            print(f"\nProcessing download: {aria} -> {save_path.name}")

            # Close any previous viewer
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.5)

            # Click open button
            btn = page.locator(f'generated-file button[aria-label="{aria}"]').first
            await btn.scroll_into_view_if_needed()
            await btn.click(force=True)
            await asyncio.sleep(1.5)

            # Find download button in drive-viewer
            dl_btn = page.locator('div[role="button"][aria-label="Download"]').first
            try:
                if await dl_btn.is_visible(timeout=3000):
                    async with page.expect_download(timeout=15000) as dl_info:
                        await dl_btn.click(force=True)
                    dl = await dl_info.value
                    await dl.save_as(str(save_path))
                    print(f" -> Successfully saved: {save_path.name} ({save_path.stat().st_size} bytes)")
                else:
                    print(" -> Download button not visible in viewer!")
            except Exception as e:
                print(f" -> Error downloading {target_filename}: {e}")

            await page.keyboard.press("Escape")
            await asyncio.sleep(0.5)

        print("\nAll downloads finished! Files in directory:")
        for item in DOWNLOAD_DIR.iterdir():
            print(f"  - {item.name} ({item.stat().st_size} bytes)")

asyncio.run(download_all())
