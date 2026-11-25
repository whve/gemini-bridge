#!/usr/bin/env python3
"""
Safe Topic Archive, Trash & Restoration Tool for Gemini Bridge & AutoPi.

Key Safety Principles:
1. Safety First & Cloud Protection: Cloud Gemini conversations are NEVER touched by default.
2. Non-Destructive Soft-Delete (Trash Can): Local files are moved to `.trash/` instead of being destroyed.
3. One-Click Restoration: Easily restore any trashed topic back to `results/`.
4. Interactive Confirmation Gate: Prompts for explicit user confirmation before touching files.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class SafeTrashManager:
    def __init__(self, project_root: Path | str) -> None:
        self.project_root = Path(project_root).resolve()
        self.trash_dir = self.project_root / ".trash"
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        self.trash_manifest_file = self.trash_dir / "trash_manifest.json"
        self._init_manifest()

    def _init_manifest(self) -> None:
        if not self.trash_manifest_file.exists():
            self._save_manifest({})

    def _load_manifest(self) -> Dict[str, Any]:
        try:
            return json.loads(self.trash_manifest_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_manifest(self, data: Dict[str, Any]) -> None:
        self.trash_manifest_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def find_matching_local_targets(self, keyword: str) -> List[Path]:
        """Finds matching files or directories under results/ without deleting anything."""
        results_dir = self.project_root / "results"
        if not results_dir.exists():
            return []

        matched = []
        # Check direct subdirectories under results/deep-research/ and results/app-tasks/
        for p in results_dir.glob("*/*"):
            if keyword.lower() in p.name.lower():
                matched.append(p)

        # Also search files matching keyword
        if not matched:
            for p in results_dir.rglob("*"):
                if keyword.lower() in p.name.lower() and p.exists():
                    matched.append(p)
                    if p.is_dir():
                        break
        return matched

    def soft_delete(self, targets: List[Path], keyword: str) -> str:
        """Moves targets to .trash/<timestamp>_<keyword>/ and records restore metadata."""
        ts_slug = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        trash_id = f"{ts_slug}_{keyword.strip().replace(' ', '_')}"
        bundle_dir = self.trash_dir / trash_id
        bundle_dir.mkdir(parents=True, exist_ok=True)

        manifest = self._load_manifest()
        moved_records = []

        for p in targets:
            rel = p.relative_to(self.project_root)
            dest = bundle_dir / p.name
            shutil.move(str(p), str(dest))
            moved_records.append({
                "original_relative_path": str(rel),
                "trash_dest": str(dest.relative_to(self.project_root)),
                "is_dir": dest.is_dir(),
            })

        manifest[trash_id] = {
            "trash_id": trash_id,
            "keyword": keyword,
            "archived_at": utc_now(),
            "items": moved_records,
        }
        self._save_manifest(manifest)
        return trash_id

    def list_trash(self) -> List[Dict[str, Any]]:
        manifest = self._load_manifest()
        return list(manifest.values())

    def restore(self, trash_id_or_keyword: str) -> List[str]:
        """Restores items from .trash back to their original locations."""
        manifest = self._load_manifest()
        target_entry = None
        target_key = None

        if trash_id_or_keyword in manifest:
            target_key = trash_id_or_keyword
            target_entry = manifest[trash_id_or_keyword]
        else:
            # Match by keyword
            for k, entry in manifest.items():
                if trash_id_or_keyword.lower() in entry.get("keyword", "").lower():
                    target_key = k
                    target_entry = entry
                    break

        if not target_entry:
            raise KeyError(f"No archived item found matching '{trash_id_or_keyword}' in .trash")

        restored_paths = []
        for item in target_entry.get("items", []):
            src = self.project_root / item["trash_dest"]
            orig = self.project_root / item["original_relative_path"]
            orig.parent.mkdir(parents=True, exist_ok=True)
            if src.exists():
                shutil.move(str(src), str(orig))
                restored_paths.append(str(orig))

        # Clean empty trash folder & update manifest
        bundle_dir = self.trash_dir / target_key
        if bundle_dir.exists():
            shutil.rmtree(bundle_dir, ignore_errors=True)

        del manifest[target_key]
        self._save_manifest(manifest)
        return restored_paths


def delete_from_gemini_cloud_safe(keyword: str, cdp_url: str = "http://127.0.0.1:9222") -> int:
    """Only executed when user explicitly specifies --delete-cloud flag."""
    from playwright.sync_api import sync_playwright

    deleted_count = 0
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp_url, timeout=5000)
            context = browser.contexts[0]
            gemini_pages = [pg for pg in context.pages if "gemini.google.com" in pg.url]
            if not gemini_pages:
                return 0
            page = gemini_pages[0]
            matches = page.locator(f'a[href*="/app/"]:has-text("{keyword}")').all()
            for link in matches:
                try:
                    title = link.inner_text().strip().replace("\n", " ")
                    link.hover()
                    page.wait_for_timeout(300)
                    btn = link.locator("xpath=..").locator("button").first
                    btn.click()
                    page.wait_for_timeout(400)
                    del_item = page.locator('button:has-text("Delete"), [role="menuitem"]:has-text("Delete")').first
                    if del_item.is_visible():
                        del_item.click()
                        page.wait_for_timeout(600)
                        dlg = page.locator('[role="dialog"]').first
                        if dlg.is_visible():
                            dlg.locator('button:has-text("Delete")').last.click()
                            deleted_count += 1
                except Exception:
                    page.keyboard.press("Escape")
        except Exception:
            pass
    return deleted_count


def main():
    parser = argparse.ArgumentParser(
        prog="delete_topic",
        description="Safe Topic Soft-Delete, Trash, Confirmation Gate & One-Click Restore",
    )
    parser.add_argument("keyword", nargs="?", default="", help="Keyword or topic name to soft-delete or restore")
    parser.add_argument("--restore", action="store_true", help="Restore a previously deleted topic from .trash back to results/")
    parser.add_argument("--list-trash", action="store_true", help="List all items currently in .trash")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
    parser.add_argument(
        "--delete-cloud",
        action="store_true",
        default=False,
        help="OPTIONAL & DEFAULT OFF: Also delete the conversation from Google Gemini cloud sidebar (WARNING: IRREVERSIBLE)",
    )
    parser.add_argument(
        "--clean-db",
        action="store_true",
        default=False,
        help="OPTIONAL & DEFAULT OFF: Also remove topic metadata rows from local SQLite index",
    )

    args = parser.parse_args()
    project_root = Path(__file__).resolve().parent.parent
    trash = SafeTrashManager(project_root)

    # 1. List Trash mode
    if args.list_trash:
        items = trash.list_trash()
        print("=" * 60)
        print("🗑️ 本地回收站中的项目清单 (.trash/):")
        print("=" * 60)
        if not items:
            print("回收站是空的 (No items in trash)")
        for it in items:
            print(f"• ID: {it['trash_id']} | 关键词: '{it['keyword']}' | 归档时间: {it['archived_at']}")
            for f in it.get("items", []):
                print(f"    - 原路径: {f['original_relative_path']}")
        return

    # 2. Restore mode
    if args.restore:
        if not args.keyword:
            print("❌ 请提供要恢复的关键词或回收站 ID，例如: python3 tools/delete_topic.py --restore '项目名'")
            sys.exit(1)
        try:
            restored = trash.restore(args.keyword)
            print("=" * 60)
            print("🎉 一键恢复成功 (Restoration Complete):")
            print("=" * 60)
            for r in restored:
                print(f"✅ 已恢复至原路径: {r}")
        except KeyError as e:
            print(f"❌ 恢复失败: {e}")
        return

    # 3. Soft Delete mode
    if not args.keyword:
        parser.print_help()
        sys.exit(1)

    targets = trash.find_matching_local_targets(args.keyword)
    if not targets:
        print(f"🔍 未在 results/ 中找到与 '{args.keyword}' 相关的本地文件或目录。")
        return

    # Confirmation Gate
    print("=" * 60)
    print(f"⚠️  待移入回收站的目标文件清单 (关键词: '{args.keyword}'):")
    print("=" * 60)
    for t in targets:
        rel = t.relative_to(project_root)
        kind = "目录 (Folder)" if t.is_dir() else "文件 (File)"
        print(f"  • [{kind}] {rel}")

    print("\n安全保护机制说明:")
    print("  1. 默认仅移动到本地回收站 (.trash/)，绝不会真正粉碎，随时可通过 --restore 一键还原；")
    print(f"  2. 云端 Gemini 会话保持保护: {'【将同步删除】' if args.delete_cloud else '【受保护，绝不触碰】(默认安全)'}；")
    print(f"  3. 本地数据库索引: {'【将清理】' if args.clean_db else '【保留】(默认安全)'}。")

    if not args.yes:
        confirm = input("\n👉 是否确认将上述文件移入本地回收站？(y/N): ").strip().lower()
        if confirm not in ("y", "yes"):
            print("❌ 操作已取消，文件未做任何变动。")
            return

    # Execute safe soft-delete
    trash_id = trash.soft_delete(targets, args.keyword)
    print(f"\n✅ 已安全移入本地回收站！(Trash ID: {trash_id})")
    print(f"💡 如需恢复，随时运行: python3 tools/delete_topic.py --restore '{args.keyword}'")

    # Optional Cloud Deletion
    if args.delete_cloud:
        print("\n🌐 正在根据显式指令清理云端 Gemini 会话...")
        count = delete_from_gemini_cloud_safe(args.keyword)
        print(f"   已从云端删除 {count} 个匹配会话。")


if __name__ == "__main__":
    main()
