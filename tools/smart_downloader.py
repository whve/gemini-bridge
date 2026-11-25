#!/usr/bin/env python3
"""
Smart Task Downloader with Versioning, Direct Link Capture & Full Dialogue Archival.
"""

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from playwright.sync_api import sync_playwright


def get_file_sha256(filepath: Path) -> str:
    if not filepath.exists() or not filepath.is_file():
        return ""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def run_smart_download():
    task_url = "https://gemini.google.com/u/2/app/fe7e40d19fc6729f?pageId=none"
    out_dir = Path("results/app-tasks/Lung3D_GWAS_Project")
    out_dir.mkdir(parents=True, exist_ok=True)
    history_dir = out_dir / "history"

    download_records = []
    print("=" * 70)
    print("🚀 SMART TASK DOWNLOADER: Lung3D-GWAS")
    print("=" * 70)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        
        # 1. Attach to the page
        matching = [pg for pg in context.pages if "fe7e40d19fc6729f" in pg.url]
        if matching:
            page = matching[0]
            print(f"[+] Attached to live tab: {page.title()} ({page.url})")
        else:
            page = context.new_page()
            page.goto(task_url, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

        # 2. Extract All Prompts (Round 1 and Round 2)
        print("\n[1/4] 📝 Archiving Full User Prompts...")
        prompt_btns = page.locator('button[aria-label="Copy prompt"]').all()
        prompts = []
        for i, pb in enumerate(prompt_btns):
            pb.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            pb.click(force=True)
            page.wait_for_timeout(600)
            ptxt = subprocess.check_output(["pbpaste"]).decode("utf-8")
            prompts.append(f"# Round {i+1} User Prompt\n\n{ptxt}\n")
            print(f"      Captured Prompt Round {i+1}: {len(ptxt)} chars")

        prompt_combined = "\n---\n\n".join(prompts)
        (out_dir / "prompt.md").write_text(prompt_combined, encoding="utf-8")

        # 3. Extract All Responses (Round 1 and Round 2)
        print("\n[2/4] 🤖 Archiving Full AI Text Responses...")
        resps = []
        model_resps = page.locator("model-response").all()
        for i, mr in enumerate(model_resps):
            btn = mr.locator('copy-button button, button[aria-label="Copy"]').first
            if btn.is_visible():
                btn.scroll_into_view_if_needed()
                page.wait_for_timeout(300)
                btn.click(force=True)
                page.wait_for_timeout(600)
                rtxt = subprocess.check_output(["pbpaste"]).decode("utf-8")
                resps.append(f"# Round {i+1} AI Response\n\n{rtxt}\n")
                print(f"      Captured Response Round {i+1}: {len(rtxt)} chars")

        resp_combined = "\n---\n\n".join(resps)
        (out_dir / "response.md").write_text(resp_combined, encoding="utf-8")

        # 4. Download Core Deliverable Files
        print("\n[3/4] 📦 Downloading Deliverable Files & Capturing Direct Links...")
        targets = [
            ("Lung3D-GWAS", "Lung3D-GWAS.zip"),
            ("submission_readiness_report", "submission_readiness_report.pdf"),
            ("submission_readiness_report.qmd", "submission_readiness_report.qmd"),
            ("references", "references.bib"),
            ("lead_snps", "lead_snps.tsv"),
            ("ld_proxy_snps", "ld_proxy_snps.tsv"),
            ("coordinate_qc", "coordinate_qc.tsv"),
            ("snp_loop_gene_links", "snp_loop_gene_links.tsv"),
            ("candidate_target_genes", "candidate_target_genes.tsv"),
            ("baseline_comparison_summary", "baseline_comparison_summary.tsv"),
            ("sensitivity_analysis", "sensitivity_analysis.tsv"),
            ("audit_issues_log", "audit_issues_log.tsv"),
            ("stress_test_results", "stress_test_results.tsv"),
            ("literature_novelty_matrix", "literature_novelty_matrix.tsv"),
            ("audit_file_manifest_and_dag", "audit_file_manifest_and_dag.tsv"),
            ("audit_findings", "audit_findings.json"),
            ("config", "config.yaml"),
        ]

        # Versioning check: prepare temp directory to inspect differences
        temp_dl_dir = out_dir / ".dl_staging"
        temp_dl_dir.mkdir(parents=True, exist_ok=True)

        for btn_label, filename in targets:
            staging_file = temp_dl_dir / filename
            final_file = out_dir / filename

            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)

            btn = page.locator(f'button[aria-label*="{btn_label}" i]').first
            if not btn.is_visible():
                continue

            btn.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            btn.click(force=True)
            page.wait_for_timeout(1800)

            dv = page.locator(".drive-viewer").first
            dl_btn = dv.locator('.drive-viewer [aria-label="Download"], .drive-viewer-content-download-button-v2').first

            direct_dl_url = "N/A"
            if dl_btn.is_visible():
                try:
                    with page.expect_download(timeout=30000) as dl_info:
                        dl_btn.click()
                    download = dl_info.value
                    direct_dl_url = download.url
                    download.save_as(str(staging_file))

                    # Version check against existing
                    new_sha = get_file_sha256(staging_file)
                    old_sha = get_file_sha256(final_file)
                    
                    status_note = "NEW"
                    if final_file.exists():
                        if new_sha == old_sha:
                            status_note = "IDENTICAL (UP-TO-DATE)"
                            staging_file.unlink(missing_ok=True)
                        else:
                            status_note = "UPDATED (SNAPSHOT CREATED)"
                            # Backup old file to history
                            history_dir.mkdir(parents=True, exist_ok=True)
                            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                            backup_path = history_dir / f"{filename}_{ts}.bak"
                            shutil.copy2(str(final_file), str(backup_path))
                            shutil.move(str(staging_file), str(final_file))
                    else:
                        shutil.move(str(staging_file), str(final_file))

                    file_sz = final_file.stat().st_size
                    print(f"      ✅ [{status_note}] {filename}: {file_sz} bytes | hash: {new_sha[:12]}...")

                    download_records.append({
                        "filename": filename,
                        "status": status_note,
                        "size_bytes": file_sz,
                        "sha256": new_sha,
                        "direct_download_url": direct_dl_url,
                        "local_path": str(final_file.relative_to(out_dir)),
                    })
                except Exception as exc:
                    print(f"      [!] Error downloading {filename}: {exc}")
            
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)

        # Cleanup staging
        shutil.rmtree(temp_dl_dir, ignore_errors=True)

        # 5. Extract ZIP
        zip_path = out_dir / "Lung3D-GWAS.zip"
        if zip_path.exists():
            print("\n[4/4] 📂 Unpacking Lung3D-GWAS.zip into extracted/...")
            extracted_dir = out_dir / "extracted"
            extracted_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(["unzip", "-o", str(zip_path), "-d", str(extracted_dir)], stdout=subprocess.DEVNULL)
            print(f"      Extracted successfully to {extracted_dir}")

        # 6. Save Download Links Markdown & Versions Manifest
        links_doc = ["# 任务资产下载链接与版本追踪清单 (Download Links & Asset Manifest)\n"]
        links_doc.append(f"- **任务来源会话 (Task URL)**: `{task_url}`")
        links_doc.append(f"- **记录时间 (Timestamp)**: `{datetime.datetime.now().isoformat()}`\n")
        links_doc.append("| 文件名称 | 状态 | 文件大小 | SHA-256 哈希 | 浏览器直接下载 URL (Direct Cloud URL) |")
        links_doc.append("| :--- | :--- | :--- | :--- | :--- |")
        for rec in download_records:
            url_display = f"`{rec['direct_download_url'][:80]}...`" if len(rec['direct_download_url']) > 80 else f"`{rec['direct_download_url']}`"
            links_doc.append(f"| **{rec['filename']}** | `{rec['status']}` | {rec['size_bytes'] / 1024:.1f} KB | `{rec['sha256'][:16]}` | {url_display} |")

        (out_dir / "download_links.md").write_text("\n".join(links_doc), encoding="utf-8")
        print("\n✨ Generated download_links.md with all direct download URLs and SHA-256 manifests!")


if __name__ == "__main__":
    run_smart_download()
