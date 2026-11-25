import asyncio
from playwright.async_api import async_playwright

async def inspect_gen_files():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = [pg for pg in context.pages if "6b529e0491802c46" in pg.url][0]

        files = await page.evaluate(r"""() => {
            const list = Array.from(document.querySelectorAll('generated-file'));
            return list.map(f => {
                const links = Array.from(f.querySelectorAll('a')).map(a => ({href: a.href, text: a.innerText, download: a.getAttribute('download')}));
                const buttons = Array.from(f.querySelectorAll('button')).map(b => ({
                    aria: b.getAttribute('aria-label') || '',
                    text: b.innerText
                }));
                return {
                    fullText: f.innerText.replace(/\n+/g, ' '),
                    outerHTML: f.outerHTML,
                    links,
                    buttons
                };
            });
        }""")
        for i, f in enumerate(files):
            print(f"File {i}: {f['fullText']}")
            print("Links:", f["links"])
            print("Buttons:", f["buttons"])
            print("HTML snippet:", f["outerHTML"][:400])
            print("--------------------------------")

asyncio.run(inspect_gen_files())
