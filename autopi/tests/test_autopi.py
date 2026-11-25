"""Automated Test Suite for AutoPi 24/7 Digital Employee Autonomy System.

Verifies:
1. Continuous iteration without human stopping or prompting
2. State persistence & crash recovery from SQLite WAL
3. Gemini Task handover, deliverable collection & verification
4. Human supervisor override (Boss Intervene, Pause, Emergency Stop)
"""

import tempfile
import time
import unittest
from pathlib import Path

from autopi.bridge_client import GeminiBridgeClient
from autopi.daemon import AutonomyDaemon
from autopi.db import AutoPiDB
from autopi.engine import AutonomyEngine
from autopi.executor import LocalWorkerExecutor


class TestAutoPiSystem(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name).resolve()
        self.db = AutoPiDB(self.root / "autopi_test.db")
        self.executor = LocalWorkerExecutor(self.root / "workspace")
        self.bridge = GeminiBridgeClient(mock_mode=True)
        self.engine = AutonomyEngine(self.db, self.executor, self.bridge)
        self.daemon = AutonomyDaemon(self.engine, heartbeat_interval_sec=0.1)

    def tearDown(self) -> None:
        if self.daemon.is_running:
            self.daemon.stop()
        self.tmp_dir.cleanup()

    def test_01_continuous_iteration_without_stopping(self) -> None:
        """Verify the engine executes multiple tasks sequentially without halting."""
        goal = self.engine.init_master_goal(
            goal_title="Autonomous Multi-Step Data Pipeline",
            goal_description="Build and test data processing components continuously.",
            initial_subtasks=[
                {"title": "Task 1: Ingest Data", "description": "Write data loader", "task_type": "local_worker"},
                {"title": "Task 2: Clean Data", "description": "Write data cleaner", "task_type": "local_worker"},
                {"title": "Task 3: Export CSV", "description": "Write exporter", "task_type": "local_worker"},
            ],
        )
        self.assertEqual(goal["progress_pct"], 0)

        # Run 3 consecutive iterations automatically
        res1 = self.engine.run_next_iteration()
        self.assertEqual(res1["verdict"], "pass")
        self.assertEqual(res1["progress_pct"], 33)

        res2 = self.engine.run_next_iteration()
        self.assertEqual(res2["verdict"], "pass")
        self.assertEqual(res2["progress_pct"], 66)

        res3 = self.engine.run_next_iteration()
        self.assertEqual(res3["verdict"], "pass")
        self.assertEqual(res3["progress_pct"], 100)

        # When all complete, next iteration automatically expands phase!
        res4 = self.engine.run_next_iteration()
        self.assertEqual(res4["status"], "phase_advanced")
        self.assertGreater(res4["new_tasks_count"], 0)

    def test_02_crash_and_exit_recovery(self) -> None:
        """Verify that if the loop crashes or exits unexpectedly, state resumes cleanly."""
        goal = self.engine.init_master_goal(
            goal_title="Resilient Long-Running Service",
            goal_description="Test crash recovery.",
            initial_subtasks=[
                {"title": "Subtask A", "description": "Run A", "task_type": "local_worker"},
                {"title": "Subtask B", "description": "Run B", "task_type": "local_worker"},
            ],
        )

        # Complete Subtask A
        self.engine.run_next_iteration()
        t_a = self.db.list_sub_tasks(goal["id"])[0]
        self.assertEqual(t_a["status"], "completed")

        # Simulate sudden process exit / restart: reconstruct entire Engine from SQLite file
        restarted_db = AutoPiDB(self.root / "autopi_test.db")
        restarted_engine = AutonomyEngine(restarted_db, self.executor, self.bridge)

        # Next pending task must automatically be Subtask B!
        next_task = restarted_db.get_next_pending_task(goal["id"])
        self.assertIsNotNone(next_task)
        self.assertEqual(next_task["title"], "Subtask B")

        # Resume execution
        resume_res = restarted_engine.run_next_iteration()
        self.assertEqual(resume_res["verdict"], "pass")
        self.assertEqual(resume_res["title"], "Subtask B")

    def test_03_gemini_task_handover_and_artifact_collection(self) -> None:
        """Verify seamless delegation to Gemini Task, deliverable download & artifact persistence."""
        goal = self.engine.init_master_goal(
            goal_title="Complex Mathematical Modeling with Gemini Task",
            goal_description="Delegate heavy synthesis to Gemini Task.",
            initial_subtasks=[
                {
                    "title": "Delegate Differential Equation Solver to Gemini",
                    "description": "Synthesize 4th-order Runge-Kutta simulator.",
                    "task_type": "gemini_delegation",
                    "expected_artifacts": ["mock_deliverable.py"],
                }
            ],
        )

        res = self.engine.run_next_iteration()
        self.assertEqual(res["verdict"], "pass")
        self.assertIn("Delegated to Gemini Task", res["worker_res"]["stdout"])

        # Verify artifact recorded in SQLite
        artifacts = self.db.list_artifacts()
        self.assertGreaterEqual(len(artifacts), 1)
        self.assertTrue(any("mock_deliverable.py" in a["file_path"] for a in artifacts))

    def test_04_human_boss_intervention_and_pause(self) -> None:
        """Verify the human owner can pause, inject priority directives, and emergency stop."""
        goal = self.engine.init_master_goal(
            goal_title="Supervised Operation",
            goal_description="Demonstrate owner intervention.",
            initial_subtasks=[{"title": "Pending Work", "description": "Some task", "task_type": "local_worker"}],
        )

        # Inject boss instruction
        self.db.inject_boss_intervention("boss_001", "Stop feature work; fix critical security bug first!", priority="critical")
        unconsumed = self.db.get_unconsumed_interventions()
        self.assertEqual(len(unconsumed), 1)

        # Next iteration consumes the instruction
        res = self.engine.run_next_iteration()
        self.assertEqual(len(self.db.get_unconsumed_interventions()), 0)

        # Test Pause
        self.daemon.pause()
        self.assertEqual(self.db.get_loop_state("status"), "paused")
        paused_res = self.engine.run_next_iteration()
        self.assertEqual(paused_res["status"], "paused")

        # Test Resume
        self.daemon.resume()
        self.assertEqual(self.db.get_loop_state("status"), "running")

        # Test Emergency Stop
        self.daemon.emergency_stop()
        self.assertEqual(self.db.get_loop_state("status"), "emergency_stopped")


if __name__ == "__main__":
    unittest.main()
