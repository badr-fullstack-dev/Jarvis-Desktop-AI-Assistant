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

    def test_executed_action_proposes_lesson(self) -> None:
        task = asyncio.run(self.api.submit_voice_or_text_task("Read a web page."))
        proposal = ActionProposal(
            task_id=task.task_id,
            capability="browser.read_page",
            intent="Read https://example.com",
            parameters={"url": "https://example.com"},
            requested_by="tester",
            evidence=["user request"],
            confidence=0.95,
        )
        result = self.api.submit_action(proposal, approved=False)
        self.assertEqual(result.status, "executed")
        candidates = self.api.supervisor.fetch_memory_candidates()
        self.assertGreaterEqual(len(candidates), 1)


if __name__ == "__main__":
    unittest.main()
