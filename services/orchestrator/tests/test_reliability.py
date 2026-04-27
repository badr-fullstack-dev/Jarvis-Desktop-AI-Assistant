"""Tests for replay / reliability / event-log health summarisation.

Covers:

* Redaction (`_scrub_dict`) — strips clipboard text, OCR text,
  transcripts, screenshot bytes, file content; replaces line/word
  lists with counts; clipping summaries.
* `task_replay` produces an ordered, redacted timeline that does NOT
  contain raw user content even when the trace did.
* `task_summary` rolls up counts (actions / failures / approvals).
* `reliability_counters` aggregates by capability and tracks workflow
  / memory transitions across multiple tasks.
* `event_log_health` returns ok=True on a fresh log, ok=False when
  the chain is tampered, and never mutates the log.
* Bridge endpoints `/tasks`, `/tasks/<id>/replay`,
  `/reliability/health`, `/reliability/counters` round-trip the data
  and surface 404 on unknown ids.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import unittest
from http.client import HTTPConnection
from pathlib import Path
from uuid import uuid4

from src.jarvis_core.api import LocalSupervisorAPI
from src.jarvis_core.bridge import start_server
from src.jarvis_core.event_log import SignedEventLog
from src.jarvis_core.models import ActionProposal, TaskRecord, TaskStatus
from src.jarvis_core.reliability import (
    _MAX_OBJECTIVE_CHARS,
    _MAX_SUMMARY_CHARS,
    _scrub_dict,
    event_log_health,
    recent_task_summaries,
    reliability_counters,
    task_replay,
    task_summary,
)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


class RedactionTests(unittest.TestCase):
    def test_clipboard_text_stripped(self) -> None:
        out = _scrub_dict({"output": {"text": "secret payload",
                                        "byte_count": 14}})
        self.assertEqual(out["output"]["byte_count"], 14)
        self.assertEqual(out["output"]["text"], "<redacted: 14 chars>")

    def test_ocr_text_and_lines_stripped(self) -> None:
        out = _scrub_dict({
            "output": {
                "ocr_text": "Hello sensitive",
                "lines": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
                "language": "en-US",
                "provider": "windows-media-ocr",
                "char_count": 15,
            },
        })
        self.assertEqual(out["output"]["ocr_text"], "<redacted: 15 chars>")
        self.assertEqual(out["output"]["lines"], {"count": 3})
        # Non-sensitive fields preserved.
        self.assertEqual(out["output"]["language"], "en-US")
        self.assertEqual(out["output"]["provider"], "windows-media-ocr")
        self.assertEqual(out["output"]["char_count"], 15)

    def test_screenshot_bytes_redacted(self) -> None:
        out = _scrub_dict({"output": {"png_bytes": b"\x89PNG..." * 100,
                                        "width": 1024}})
        self.assertEqual(out["output"]["width"], 1024)
        self.assertTrue(out["output"]["png_bytes"].startswith("<redacted:"))

    def test_filesystem_write_content_redacted(self) -> None:
        out = _scrub_dict({"parameters": {"path": "runtime/sandbox/x.txt",
                                          "content": "hello world"}})
        self.assertEqual(out["parameters"]["path"], "runtime/sandbox/x.txt")
        self.assertEqual(out["parameters"]["content"], "<redacted: 11 chars>")

    def test_browser_text_excerpt_redacted(self) -> None:
        # Both keys we treat as sensitive — snake_case and camelCase.
        out = _scrub_dict({
            "context": {
                "url": "https://example.com",
                "title": "Example Domain",
                "text_excerpt": "This is the page body...",
                "textExcerpt": "duplicate field",
            },
        })
        self.assertEqual(out["context"]["url"], "https://example.com")
        # Page titles are not user-content bodies; we keep them.
        self.assertEqual(out["context"]["title"], "Example Domain")
        self.assertTrue(out["context"]["text_excerpt"].startswith("<redacted:"))
        self.assertTrue(out["context"]["textExcerpt"].startswith("<redacted:"))

    def test_empty_strings_pass_through(self) -> None:
        out = _scrub_dict({"output": {"text": "", "byte_count": 0}})
        self.assertEqual(out["output"]["text"], "")
        self.assertEqual(out["output"]["byte_count"], 0)

    def test_idempotent(self) -> None:
        once = _scrub_dict({"output": {"text": "secret"}})
        twice = _scrub_dict(once)
        self.assertEqual(once, twice)


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def _task_with_trace(*entries) -> TaskRecord:
    t = TaskRecord(objective="test", source="text",
                   status=TaskStatus.RUNNING)
    t.trace = list(entries)
    return t


class TaskReplayTests(unittest.TestCase):
    def test_orders_events_by_index(self) -> None:
        t = _task_with_trace(
            {"event": "plan.evaluated", "plan": {"status": "mapped",
                                                   "capability": "filesystem.read",
                                                   "matchedRule": "read.path"}},
            {"event": "action.executed",
             "result": {"status": "executed",
                        "summary": "read ok",
                        "proposal": {"capability": "filesystem.read"},
                        "verification": {"ok": True}}},
        )
        replay = task_replay(t)
        self.assertEqual(len(replay["events"]), 2)
        self.assertEqual(replay["events"][0]["index"], 0)
        self.assertEqual(replay["events"][1]["index"], 1)

    def test_summary_clipped(self) -> None:
        long_summary = "x" * (_MAX_SUMMARY_CHARS + 50)
        t = _task_with_trace({
            "event": "action.executed",
            "result": {"status": "executed", "summary": long_summary,
                       "proposal": {"capability": "filesystem.read"}},
        })
        replay = task_replay(t)
        self.assertLessEqual(len(replay["events"][0]["summary"]),
                             _MAX_SUMMARY_CHARS)
        self.assertTrue(replay["events"][0]["summary"].endswith("…"))

    def test_clipboard_text_does_not_leak_into_replay(self) -> None:
        # Build a trace that carries clipboard text in the action result.
        t = _task_with_trace({
            "event": "action.executed",
            "result": {
                "status": "executed",
                "summary": "Read 14 chars from clipboard",
                "proposal": {"capability": "desktop.clipboard_read"},
                "output": {"text": "secret-payload",
                           "byte_count": 14, "truncated": False,
                           "dry_run": False},
            },
        })
        replay = task_replay(t)
        payload = replay["events"][0]["payload"]
        self.assertNotIn("secret-payload", json.dumps(payload))
        # byte_count metadata is fine; text is redacted.
        self.assertEqual(payload["result"]["output"]["byte_count"], 14)
        self.assertTrue(payload["result"]["output"]["text"].startswith("<redacted:"))

    def test_ocr_text_and_lines_redacted(self) -> None:
        t = _task_with_trace({
            "event": "action.executed",
            "result": {
                "status": "executed",
                "summary": "OCR ok",
                "proposal": {"capability": "desktop.ocr_foreground"},
                "output": {
                    "text": "very secret OCR output",
                    "lines": [{"text": "a"}, {"text": "b"}],
                    "char_count": 22,
                    "language": "en-US",
                    "provider": "fake",
                },
            },
        })
        replay = task_replay(t)
        out = replay["events"][0]["payload"]["result"]["output"]
        self.assertNotIn("very secret OCR output", json.dumps(replay))
        self.assertEqual(out["lines"], {"count": 2})
        self.assertEqual(out["language"], "en-US")
        self.assertEqual(out["char_count"], 22)

    def test_verification_ok_surfaces(self) -> None:
        t = _task_with_trace({
            "event": "action.executed",
            "result": {"status": "executed", "summary": "ok",
                        "proposal": {"capability": "filesystem.read"},
                        "verification": {"ok": True, "checked": ["filesystem.read"]}},
        })
        replay = task_replay(t)
        self.assertTrue(replay["events"][0]["verificationOk"])

    def test_objective_clipped(self) -> None:
        t = _task_with_trace()
        t.objective = "z" * (_MAX_OBJECTIVE_CHARS + 100)
        replay = task_replay(t)
        self.assertLessEqual(len(replay["objective"]), _MAX_OBJECTIVE_CHARS)


# ---------------------------------------------------------------------------
# Summaries / counters
# ---------------------------------------------------------------------------


def _exec_event(cap: str, status: str = "executed") -> dict:
    return {
        "event": "action.executed",
        "result": {"status": status, "summary": f"{cap} {status}",
                    "proposal": {"capability": cap}},
    }


class TaskSummaryAndCountersTests(unittest.TestCase):
    def test_task_summary_rolls_up_counts(self) -> None:
        t = _task_with_trace(
            _exec_event("filesystem.read"),
            _exec_event("filesystem.write", status="failed"),
            {"event": "approval.requested",
             "approval": {"capability": "app.install", "risk_tier": 2}},
            {"event": "approval.denied",
             "approval": {"capability": "app.install", "reason": "no"}},
        )
        s = task_summary(t)
        self.assertEqual(s["actionCount"], 2)
        self.assertEqual(s["failureCount"], 1)
        self.assertEqual(s["approvalCount"], 1)
        self.assertEqual(s["denialCount"], 1)
        self.assertEqual(s["lastCapability"], "filesystem.write")

    def test_recent_summaries_are_newest_first_and_capped(self) -> None:
        tasks: dict = {}
        for i in range(5):
            t = TaskRecord(objective=f"task {i}", source="text")
            # mimic ordering: created_at strings with embedded counter.
            t.created_at = f"2026-04-27T00:00:0{i}+00:00"
            tasks[t.task_id] = t
        out = recent_task_summaries(tasks, limit=3)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0]["objective"], "task 4")

    def test_reliability_counters_aggregate_by_capability(self) -> None:
        t1 = _task_with_trace(
            _exec_event("filesystem.read"),
            _exec_event("filesystem.read", status="failed"),
        )
        t2 = _task_with_trace(
            _exec_event("filesystem.read"),
            {"event": "action.blocked",
             "result": {"proposal": {"capability": "filesystem.move"},
                         "decision": {"reason": "tier 2"}}},
            {"event": "workflow.completed",
             "workflow": {"patternId": "wf.open_and_read",
                           "steps": [{"capability": "browser.navigate"}]}},
        )
        c = reliability_counters({"a": t1, "b": t2})
        self.assertEqual(c["byCapability"]["filesystem.read"]["executed"], 2)
        self.assertEqual(c["byCapability"]["filesystem.read"]["failed"], 1)
        self.assertEqual(c["byCapability"]["filesystem.move"]["blocked"], 1)
        self.assertEqual(c["totals"]["actions"], 4)
        self.assertEqual(c["totals"]["failures"], 2)
        self.assertEqual(c["workflows"]["wf.open_and_read"]["completed"], 1)


# ---------------------------------------------------------------------------
# Event-log health
# ---------------------------------------------------------------------------


class EventLogHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(__file__).resolve().parents[3] / "runtime" / f"rel-{uuid4()}"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.path = self.tmp / "events.jsonl"
        self.log = SignedEventLog(self.path, secret="test-secret")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fresh_log_is_healthy(self) -> None:
        h = event_log_health(self.log)
        self.assertTrue(h["ok"])
        self.assertEqual(h["recordCount"], 0)
        self.assertEqual(h["lengthBytes"], 0)
        self.assertIsNone(h["lastEventAt"])

    def test_appended_events_count_correctly(self) -> None:
        self.log.append("alpha", {"k": 1})
        self.log.append("beta", {"k": 2})
        h = event_log_health(self.log)
        self.assertTrue(h["ok"])
        self.assertEqual(h["recordCount"], 2)
        self.assertGreater(h["lengthBytes"], 0)
        self.assertIsNotNone(h["lastEventAt"])

    def test_tampered_log_reports_unhealthy(self) -> None:
        self.log.append("alpha", {"k": 1})
        self.log.append("beta", {"k": 2})
        # Tamper: rewrite a payload but keep the signature intact —
        # verify_chain should now fail.
        lines = self.path.read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        first["payload"] = {"k": 999}
        lines[0] = json.dumps(first, sort_keys=True)
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        h = event_log_health(self.log)
        self.assertFalse(h["ok"])

    def test_health_check_does_not_mutate_log(self) -> None:
        self.log.append("alpha", {"k": 1})
        before = self.path.read_bytes()
        for _ in range(3):
            event_log_health(self.log)
        after = self.path.read_bytes()
        self.assertEqual(before, after)


# ---------------------------------------------------------------------------
# Bridge endpoints
# ---------------------------------------------------------------------------


def _http_get(host, port, path):
    conn = HTTPConnection(host, port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    return resp.status, json.loads(resp.read().decode("utf-8") or "{}")


class ReliabilityBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        workspace = Path(__file__).resolve().parents[3]
        self.root = workspace / "runtime" / f"rel-br-{uuid4()}"
        (self.root / "configs").mkdir(parents=True, exist_ok=True)
        (self.root / "configs" / "policy.default.json").write_text(
            (workspace / "configs" / "policy.default.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.api = LocalSupervisorAPI(self.root)
        self.server = start_server(self.api, port=0, daemon=True)
        self.host, self.port = self.server.server_address[:2]

    def tearDown(self) -> None:
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass
        shutil.rmtree(self.root, ignore_errors=True)

    def _drive_simple_task(self):
        async def run():
            return await self.api.submit_voice_or_text_task(
                "list runtime/sandbox")
        task = asyncio.run(run())
        return task

    def test_recent_tasks_endpoint_lists_summaries(self) -> None:
        self._drive_simple_task()
        code, body = _http_get(self.host, self.port, "/tasks?limit=10")
        self.assertEqual(code, 200)
        self.assertGreaterEqual(len(body["items"]), 1)
        item = body["items"][0]
        self.assertIn("taskId", item)
        self.assertIn("actionCount", item)
        self.assertIn("status", item)

    def test_replay_endpoint_returns_redacted_events(self) -> None:
        task = self._drive_simple_task()
        code, body = _http_get(self.host, self.port,
                               f"/tasks/{task.task_id}/replay")
        self.assertEqual(code, 200)
        self.assertEqual(body["taskId"], task.task_id)
        self.assertGreaterEqual(len(body["events"]), 1)
        # The replay JSON must never contain raw "lines": [{"text":...}] —
        # if any OCR or text payload is present, it has been redacted.
        raw = json.dumps(body)
        self.assertNotIn("\"text\": \"secret", raw)

    def test_replay_unknown_task_returns_404(self) -> None:
        code, _ = _http_get(self.host, self.port,
                             "/tasks/task-does-not-exist/replay")
        self.assertEqual(code, 404)

    def test_reliability_health_endpoint(self) -> None:
        self._drive_simple_task()
        code, body = _http_get(self.host, self.port, "/reliability/health")
        self.assertEqual(code, 200)
        self.assertIn("ok", body)
        self.assertIn("recordCount", body)
        # A fresh API run produced at least task.created + plan.evaluated.
        self.assertGreater(body["recordCount"], 0)

    def test_reliability_counters_endpoint(self) -> None:
        self._drive_simple_task()
        code, body = _http_get(self.host, self.port, "/reliability/counters")
        self.assertEqual(code, 200)
        self.assertIn("byCapability", body)
        self.assertIn("totals", body)
        self.assertGreaterEqual(body["totals"]["tasks"], 1)


if __name__ == "__main__":
    unittest.main()
