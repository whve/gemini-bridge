"""Command-line interface for AutoPi 24/7 Autonomy System."""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from .bridge_client import GeminiBridgeClient
from .daemon import AutonomyDaemon
from .db import AutoPiDB
from .engine import AutonomyEngine
from .executor import LocalWorkerExecutor
from .server import run_supervisor_server


def setup_system(
    base_dir: Path,
    mock_bridge: bool = False,
    heartbeat_sec: float = 3.0,
) -> tuple[AutoPiDB, AutonomyEngine, AutonomyDaemon]:
    db = AutoPiDB(base_dir / "data" / "autopi_state.db")
    executor = LocalWorkerExecutor(base_dir / "workspace")
    bridge = GeminiBridgeClient(mock_mode=mock_bridge)
    engine = AutonomyEngine(db, executor, bridge)
    daemon = AutonomyDaemon(engine, heartbeat_interval_sec=heartbeat_sec)
    return db, engine, daemon


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        prog="autopi",
        description="AutoPi: 24/7 Autonomous Digital Employee & Supervisor Hub",
    )
    parser.add_argument("--dir", default="./.autopi", help="AutoPi state & workspace directory")

    sub = parser.add_subparsers(dest="cmd", required=True)

    # 1. start / serve
    p_start = sub.add_parser("start", help="Start 24/7 background daemon & supervisor web console")
    p_start.add_argument("--dir", default=None, help="AutoPi state & workspace directory")
    p_start.add_argument("--port", type=int, default=9100, help="Supervisor console port (default: 9100)")
    p_start.add_argument("--host", default="127.0.0.1", help="Bind host")
    p_start.add_argument("--heartbeat", type=float, default=3.0, help="Heartbeat interval in seconds")
    p_start.add_argument("--mock-bridge", action="store_true", help="Use mock Gemini bridge for offline testing")
    p_start.add_argument("--goal-title", default="全网前沿科研发现与双平台自媒体矩阵全自动运营", help="Default goal title")
    p_start.add_argument("--goal-desc", default="持续调研前沿科技与学术研报，自动生成代码与图表，同步维护小红书与X.com草稿与排期发布。", help="Default goal description")

    # 2. status
    sub.add_parser("status", help="Print current loop telemetry & active goal")

    # 3. goal
    p_goal = sub.add_parser("goal", help="Set or replace the master goal")
    p_goal.add_argument("title", help="Goal title")
    p_goal.add_argument("description", help="Goal details and constraints")

    # 4. intervene
    p_int = sub.add_parser("intervene", help="Inject boss priority directive into active loop")
    p_int.add_argument("instruction", help="Instruction text")
    p_int.add_argument("--critical", action="store_true", help="Mark as critical priority")

    # 5. pause / resume
    sub.add_parser("pause", help="Pause the autonomous loop")
    sub.add_parser("resume", help="Resume the autonomous loop")

    args = parser.parse_args()
    raw_dir = getattr(args, "dir", None) or "./.autopi"
    base_dir = Path(raw_dir).resolve()
    base_dir.mkdir(parents=True, exist_ok=True)

    if args.cmd == "start":
        db, engine, daemon = setup_system(base_dir, mock_bridge=args.mock_bridge, heartbeat_sec=args.heartbeat)
        
        # Initialize default goal if no active goal exists
        if not db.get_active_goal():
            engine.init_master_goal(
                goal_title=args.goal_title,
                goal_description=args.goal_desc,
                initial_subtasks=[
                    {
                        "title": "里程碑 1: 全网可控核聚变与大模型生信前沿情报自动化巡检",
                        "description": "调用 Gemini Task 收集过去 24 小时内的最新预印本与 GitHub 开源成果。",
                        "task_type": "gemini_delegation",
                        "expected_artifacts": ["intel_brief.md"],
                    },
                    {
                        "title": "里程碑 2: 核心代码验证与自动化测试基准复现",
                        "description": "在本地沙箱运行分析脚本，生成准确度与性能基准报告。",
                        "task_type": "local_worker",
                        "expected_artifacts": ["benchmark_results.csv"],
                    },
                    {
                        "title": "里程碑 3: 双平台多模态发布包排版与草稿箱同步",
                        "description": "自动排版小红书笔记（带高斯打字延迟）与 X.com 串联推文，存入草稿队列。",
                        "task_type": "local_worker",
                        "expected_artifacts": ["social_drafts_manifest.json"],
                    },
                ],
            )
            print(f"✨ Initialized Master Goal: {args.goal_title}")

        # Start 24/7 background loop thread
        daemon.start()

        # Start supervisor server on main thread
        server = run_supervisor_server(daemon, host=args.host, port=args.port)
        print("=" * 70)
        print(f"🚀 AutoPi 24/7 Digital Employee is ACTIVE!")
        print(f"🌐 Boss Supervisor Console : http://{args.host}:{args.port}")
        print(f"📁 State & Workspace      : {base_dir}")
        print(f"⚡ Heartbeat Interval      : {args.heartbeat}s")
        print("=" * 70)
        print("按 Ctrl+C 可安全退出主控台（后台状态已持久化至 SQLite WAL）\n")

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down AutoPi cleanly...")
            daemon.stop()
            server.server_close()
            sys.exit(0)

    elif args.cmd == "status":
        db, engine, daemon = setup_system(base_dir)
        goal = db.get_active_goal()
        loop_status = db.get_loop_state("status", "running")
        tasks = db.list_sub_tasks(goal["id"]) if goal else []
        print(json.dumps({
            "loop_status": loop_status,
            "active_goal": goal.get("title") if goal else None,
            "progress_pct": goal.get("progress_pct") if goal else 0,
            "tasks_total": len(tasks),
            "tasks_completed": sum(1 for t in tasks if t["status"] == "completed"),
        }, indent=2, ensure_ascii=False))

    elif args.cmd == "goal":
        db, engine, daemon = setup_system(base_dir)
        goal = engine.init_master_goal(args.title, args.description)
        print(f"✅ Set new master goal: {goal['title']} (ID: {goal['id']})")

    elif args.cmd == "intervene":
        db, engine, daemon = setup_system(base_dir)
        import uuid
        iid = f"boss_{uuid.uuid4().hex[:8]}"
        priority = "critical" if args.critical else "normal"
        db.inject_boss_intervention(iid, args.instruction, priority=priority)
        print(f"✅ Injected Boss Directive [{priority.upper()}]: {args.instruction}")

    elif args.cmd == "pause":
        db, engine, daemon = setup_system(base_dir)
        db.set_loop_state("status", "paused")
        print("⏸️ Loop paused.")

    elif args.cmd == "resume":
        db, engine, daemon = setup_system(base_dir)
        db.set_loop_state("status", "running")
        print("▶️ Loop resumed.")


if __name__ == "__main__":
    main()
