from __future__ import annotations

import asyncio
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from src.jarvis_core.api import LocalSupervisorAPI
from src.jarvis_core.event_log import SignedEventLog
from src.jarvis_core.models import ActionProposal


class RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        workspace_root = Path(__file__).resolve().parents[3]
        self.root = workspace_root / "runtime" / f"test-{uuid4()}"
        (self.root / "configs").mkdir(parents=True, exist_ok=True)
        source_config = workspace_root / "configs" / "policy.default.json"
        (self.root / "configs" / "policy.default.json").write_text(source_config.read_text(encoding="utf-8"), encoding="utf-8")
        self.api = LocalSupervisorAPI(self.root)

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def test_submit_task_generates_plan_and_trace(self) -> None:
        task = asyncio.run(self.api.submit_voice_or_text_task("Review a download before installing it.", source="voice"))
        self.assertEqual(task.status.value, "running")
        self.assertGreaterEqual(len(task.plan), 1)
        self.assertGreaterEqual(len(task.trace), 4)

    def test_tier_two_action_requires_approval(self) -> None:
        task = asyncio.run(self.api.submit_voice_or_text_task("Install a browser extension."))
        proposal = ActionProposal(
            task_id=task.task_id,
            capability="app.install",
            intent="Install a browser extension from a trusted vendor.",
            parameters={"name": "Trusted Extension"},
            requested_by="tester",
            evidence=["vendor page"],
            confidence=0.91,
        )
        result = self.api.submit_action(proposal, approved=False)
        self.assertEqual(result.status, "awaiting_approval")
        self.assertEqual(self.api.supervisor.inspect_task(task.task_id)["status"], "blocked")

    def test_blocked_pattern_stays_blocked_even_if_approved_false(self) -> None:
        task = asyncio.run(self.api.submit_voice_or_text_task("Delete system files."))
        proposal = ActionProposal(
            task_id=task.task_id,
            capability="system.delete",
            intent="Delete System32 and format disk",
            parameters={"path": "C:/Windows/System32"},
            requested_by="tester",
            evidence=["malicious request"],
            confidence=0.99,
        )
        result = self.api.submit_action(proposal, approved=False)
        self.assertEqual(result.status, "blocked")

    def test_event_log_chain_verifies(self) -> None:
        log = SignedEventLog(self.root / "runtime" / "events.jsonl", secret="secret")
        log.append("alpha", {"ok": True})
        log.append("beta", {"ok": True})
        self.assertTrue(log.verify_chain())

    def test_failed_action_proposes_tool_reliability_lesson(self) -> None:
        # The reflector now produces a *targeted* tool memory when an
        # action fails, instead of the previous boilerplate-after-every-
        # action behaviour. Read an absolute path that lives outside the
        # configured read roots so the adapter raises a scope error.
        task = asyncio.run(self.api.submit_voice_or_text_task("Test reflection."))
        outside = (Path(__file__).resolve().parent / "test_runtime.py")
        proposal = ActionProposal(
            task_id=task.task_id,
            capability="filesystem.read",
            intent="read outside scope",
            parameters={"path": str(outside)},
            requested_by="tester",
            evidence=["test"],
            confidence=0.95,
        )
        result = self.api.submit_action(proposal, approved=False)
        self.assertEqual(result.status, "failed")
        candidates = self.api.supervisor.fetch_memory_candidates()
        # Exactly one tool-reliability candidate should be filed for
        # the failure — no spam.
        tool_candidates = [c for c in candidates if c.get("kind") == "tool"]
        self.assertGreaterEqual(len(tool_candidates), 1)
        self.assertIn("filesystem.read", tool_candidates[0]["summary"])

    def test_clean_action_does_not_spam_lessons(self) -> None:
        # The old reflector proposed a generic lesson after EVERY
        # successful action. The new reflector should NOT do that.
        # Pick an objective that the planner ignores entirely so the
        # only thing the reflector could fire on is the action itself.
        task = asyncio.run(self.api.submit_voice_or_text_task("clean run check"))
        proposal = ActionProposal(
            task_id=task.task_id,
            capability="filesystem.read",
            intent="read policy",
            parameters={"path": str(self.root / "configs" / "policy.default.json")},
            requested_by="tester",
            evidence=["test"],
            confidence=0.95,
        )
        result = self.api.submit_action(proposal, approved=False)
        self.assertEqual(result.status, "executed")
        candidates = self.api.supervisor.fetch_memory_candidates()
        # No lesson/tool/profile/operational candidate should be filed —
        # this is a normal, uneventful read.
        self.assertEqual(len(candidates), 0,
                         f"Reflector proposed unexpected lessons: {candidates}")


if __name__ == "__main__":
    unittest.main()
