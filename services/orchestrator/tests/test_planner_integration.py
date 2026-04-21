"""Integration tests: planner + supervisor + gateway.

Ensures `submit_voice_or_text_task` auto-proposes through the existing
gateway path, that tier gating still applies, and that voice-submitted
transcripts follow the same flow as typed text.
"""

from __future__ import annotations

import asyncio
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from src.jarvis_core.api import LocalSupervisorAPI
from src.jarvis_core.voice import TranscriptionProvider


class _FixedTranscriber(TranscriptionProvider):
    def __init__(self, transcript: str) -> None:
        self.transcript = transcript
        self.name = "fixed-test"

    def transcribe(self, audio_bytes: bytes, mime: str) -> str:  # noqa: D401
        return self.transcript


class PlannerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        workspace_root = Path(__file__).resolve().parents[3]
        self.root = workspace_root / "runtime" / f"planner-int-{uuid4()}"
        (self.root / "configs").mkdir(parents=True, exist_ok=True)
        source_config = workspace_root / "configs" / "policy.default.json"
        (self.root / "configs" / "policy.default.json").write_text(
            source_config.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (self.root / "runtime" / "sandbox").mkdir(parents=True, exist_ok=True)
        self.api = LocalSupervisorAPI(self.root)

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def _submit(self, text: str, source: str = "text"):
        return asyncio.run(self.api.submit_voice_or_text_task(text, source=source))

    # --- Tier 0 auto-execute via planner --------------------------------------

    def test_list_configs_auto_executes_tier0(self) -> None:
        task = self._submit(f"list files in {self.root / 'configs'}")
        plan = task.context.get("plan")
        self.assertIsNotNone(plan)
        self.assertEqual(plan["status"], "mapped")
        self.assertEqual(plan["capability"], "filesystem.list")

        action = task.context.get("planAction")
        self.assertIsNotNone(action)
        self.assertEqual(action["capability"], "filesystem.list")
        self.assertEqual(action["status"], "executed")

    def test_read_sandbox_file_auto_executes(self) -> None:
        target = self.root / "runtime" / "sandbox" / "note.txt"
        target.write_text("hello sandbox", encoding="utf-8")
        task = self._submit(f"read {target}")
        self.assertEqual(task.context["plan"]["capability"], "filesystem.read")
        self.assertEqual(task.context["planAction"]["status"], "executed")

    # --- Tier 1 high-confidence write executes via planner --------------------

    def test_write_to_sandbox_auto_executes_on_high_confidence(self) -> None:
        # Use a sandbox-rooted absolute path so the gateway's scope check
        # resolves cleanly regardless of the test process's CWD. The
        # planner's 'write X to <path>' rule accepts any sandbox-prefixed
        # path; we stage the input with a real absolute target.
        target = self.root / "runtime" / "sandbox" / "hello.txt"
        sandbox_abs = str(target).replace("\\", "/")
        task = self._submit(f"write hello to {sandbox_abs}")
        self.assertEqual(task.context["plan"]["capability"], "filesystem.write")
        action = task.context["planAction"]
        # filesystem.write is Tier 1 (conditional approval). The planner
        # emits high confidence, so this should execute, not queue.
        self.assertEqual(action["status"], "executed")
        self.assertTrue(target.exists())

    # --- Unsupported / clarification: NO auto-propose -------------------------

    def test_unsupported_text_records_plan_but_does_not_propose(self) -> None:
        task = self._submit("please summarize my week")
        self.assertEqual(task.context["plan"]["status"], "unsupported")
        self.assertNotIn("planAction", task.context)

    def test_clarification_needed_does_not_propose(self) -> None:
        task = self._submit("open it")
        self.assertEqual(task.context["plan"]["status"], "clarification_needed")
        self.assertNotIn("planAction", task.context)

    # --- Trace event is emitted for every submission --------------------------

    def test_plan_evaluated_trace_event_is_recorded(self) -> None:
        task = self._submit("open https://example.com")
        events = [e.get("event") for e in task.trace]
        self.assertIn("plan.evaluated", events)

    # --- Voice path follows the same planner flow -----------------------------

    def test_voice_transcript_flows_through_planner(self) -> None:
        # Swap in a fixed transcriber so the voice session is deterministic.
        # Use a Tier-0 read intent so nothing is launched as a real process.
        spoken = f"list files in {self.root / 'configs'}"
        self.api.voice.provider = _FixedTranscriber(spoken)
        self.api.voice.start()
        self.api.voice.stop(b"\x00" * 1024, "audio/webm")
        transcript = self.api.voice.consume_transcript()
        self.assertEqual(transcript, spoken)

        task = asyncio.run(
            self.api.submit_voice_or_text_task(transcript, source="voice")
        )
        # Voice path must go through the same planner + gateway flow as text.
        self.assertEqual(task.context["plan"]["capability"], "filesystem.list")
        self.assertEqual(task.context["planAction"]["status"], "executed")


if __name__ == "__main__":
    unittest.main()
