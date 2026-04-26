"""Tests for curated memory + reflection.

Covers:

* Memory lifecycle (propose → approve / reject / expire / delete).
* Sensitive-payload filter (clipboard text, OCR text, transcript,
  screenshot bytes, free-form summaries) all rejected by the store.
* Reflector emissions: tool note on failure, operational on completed
  workflow, profile on explicit-preference objective, lesson on
  clarification_needed; deduplication within a task.
* ApprovedMemoryHints filters approved memory by capability /
  matched_rule and never escalates planning.
* Memory does NOT bypass the PolicyEngine (Tier 2 stays Tier 2 even
  when an approved memory mentions the capability).
* Bridge endpoints round-trip approve / reject / expire and return
  HTTP 404 on unknown ids.
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
from src.jarvis_core.memory import (
    MemoryRejectedError,
    MemoryStore,
    STATUS_APPROVED,
    STATUS_CANDIDATE,
    STATUS_EXPIRED,
    STATUS_REJECTED,
)
from src.jarvis_core.models import ActionProposal, MemoryItem, TaskRecord
from src.jarvis_core.reflection import (
    ApprovedMemoryHints,
    Reflector,
    is_sensitive_payload,
)


def _empty_store(tmp: Path) -> MemoryStore:
    return MemoryStore(tmp)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class MemoryLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(__file__).resolve().parents[3] / "runtime" / f"mem-{uuid4()}"
        self.store = _empty_store(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _propose(self, kind: str = "lesson", summary: str = "test memory") -> str:
        item = self.store.propose_lesson(
            summary=summary, evidence=["unit-test"],
            trust_score=0.5, kind=kind,
        )
        return item.memory_id

    def test_propose_creates_candidate(self) -> None:
        memory_id = self._propose()
        row = self.store.get(memory_id)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["status"], STATUS_CANDIDATE)
        self.assertEqual(row["kind"], "lesson")

    def test_approve_flips_status_and_records_reviewer(self) -> None:
        mid = self._propose()
        self.store.approve(mid, reviewed_by="tester")
        row = self.store.get(mid)
        assert row is not None
        self.assertEqual(row["status"], STATUS_APPROVED)
        self.assertEqual(row["reviewed_by"], "tester")
        self.assertIsNotNone(row.get("reviewed_at"))

    def test_reject_records_reason(self) -> None:
        mid = self._propose()
        self.store.reject(mid, reason="not actionable")
        row = self.store.get(mid)
        assert row is not None
        self.assertEqual(row["status"], STATUS_REJECTED)
        self.assertEqual(row["review_reason"], "not actionable")

    def test_expire_records_status(self) -> None:
        mid = self._propose()
        self.store.expire(mid, reason="stale")
        row = self.store.get(mid)
        assert row is not None
        self.assertEqual(row["status"], STATUS_EXPIRED)

    def test_delete_removes_row(self) -> None:
        mid = self._propose()
        self.assertTrue(self.store.delete(mid))
        self.assertIsNone(self.store.get(mid))
        self.assertFalse(self.store.delete(mid))  # second time: False

    def test_unknown_id_raises(self) -> None:
        with self.assertRaises(KeyError):
            self.store.approve("memory:not-real")

    def test_unknown_kind_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.propose(MemoryItem(kind="WHATEVER", summary="x",
                                          details={}, evidence=[],
                                          trust_score=0.1))

    def test_filter_by_status_and_kind(self) -> None:
        a = self._propose(kind="lesson")
        b = self._propose(kind="profile", summary="user pref")
        self.store.approve(a)
        self.assertEqual(len(self.store.list(status="approved")), 1)
        self.assertEqual(len(self.store.list(status="candidate")), 1)
        self.assertEqual(len(self.store.list(kind="profile")), 1)
        self.assertEqual(len(self.store.list(kind="profile", status="approved")), 0)


# ---------------------------------------------------------------------------
# Sensitive-payload filter
# ---------------------------------------------------------------------------


class SensitivePayloadFilterTests(unittest.TestCase):
    def test_pure_summary_with_clipboard_phrase_blocked(self) -> None:
        item = MemoryItem(kind="lesson", summary="Clipboard contains: HelloSecret",
                          details={}, evidence=[], trust_score=0.5)
        self.assertIsNotNone(is_sensitive_payload(item))

    def test_ocr_text_in_details_blocked(self) -> None:
        item = MemoryItem(kind="lesson",
                          summary="Page summary", details={"ocr_text": "secret bank statement"},
                          evidence=[], trust_score=0.5)
        self.assertIsNotNone(is_sensitive_payload(item))

    def test_transcript_in_details_blocked(self) -> None:
        item = MemoryItem(kind="profile",
                          summary="Liked the response",
                          details={"transcript": "I said please open notepad"},
                          evidence=[], trust_score=0.5)
        self.assertIsNotNone(is_sensitive_payload(item))

    def test_screenshot_bytes_blocked(self) -> None:
        item = MemoryItem(kind="lesson", summary="screenshot inspection",
                          details={"screenshot": "iVBORw0KGgo..."},
                          evidence=[], trust_score=0.5)
        self.assertIsNotNone(is_sensitive_payload(item))

    def test_evidence_phrase_blocked(self) -> None:
        item = MemoryItem(kind="lesson", summary="planner clarified",
                          details={},
                          evidence=["clipboard contained credit card number"],
                          trust_score=0.5)
        self.assertIsNotNone(is_sensitive_payload(item))

    def test_long_summary_blocked(self) -> None:
        item = MemoryItem(kind="lesson", summary="x" * 900,
                          details={}, evidence=[], trust_score=0.1)
        self.assertIsNotNone(is_sensitive_payload(item))

    def test_safe_metadata_passes(self) -> None:
        item = MemoryItem(kind="tool",
                          summary="filesystem.write failed with error_type=ScopeError",
                          details={"capability": "filesystem.write",
                                   "error_type": "ScopeError",
                                   "task_id": "task:abc"},
                          evidence=["task:abc"], trust_score=0.5)
        self.assertIsNone(is_sensitive_payload(item))

    def test_filter_enforced_at_store_level(self) -> None:
        tmp = Path(__file__).resolve().parents[3] / "runtime" / f"mem-{uuid4()}"
        try:
            store = _empty_store(tmp)
            sneaky = MemoryItem(kind="lesson", summary="ok summary",
                                details={"text": "raw clipboard contents"},
                                evidence=[], trust_score=0.5)
            with self.assertRaises(MemoryRejectedError):
                store.propose(sneaky)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Reflector
# ---------------------------------------------------------------------------


def _task(objective: str = "do something", **ctx) -> TaskRecord:
    t = TaskRecord(objective=objective, source="test")
    t.context.update(ctx)
    return t


def _action_executed(capability: str, status: str, output: dict | None = None,
                     summary: str = "") -> dict:
    return {
        "event": "action.executed",
        "result": {
            "status": status,
            "summary": summary or f"{capability} {status}",
            "proposal": {"capability": capability, "task_id": "t"},
            "output": output or {},
        },
    }


class ReflectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(__file__).resolve().parents[3] / "runtime" / f"mem-{uuid4()}"
        self.store = _empty_store(self.tmp)
        self.reflector = Reflector(self.store)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_failed_action_yields_tool_note(self) -> None:
        task = _task("read configs/policy.default.json")
        task.trace.append(_action_executed(
            "filesystem.read", "failed",
            output={"error_type": "ScopeError", "error": "outside read root"},
        ))
        proposed = self.reflector.reflect_on_task(task)
        self.assertEqual(len(proposed), 1)
        self.assertEqual(proposed[0]["kind"], "tool")
        self.assertIn("filesystem.read", proposed[0]["summary"])
        self.assertIn("ScopeError", proposed[0]["summary"])

    def test_clean_action_yields_no_proposal(self) -> None:
        task = _task("read configs/policy.default.json")
        task.trace.append(_action_executed("filesystem.read", "executed"))
        proposed = self.reflector.reflect_on_task(task)
        self.assertEqual(proposed, [])

    def test_workflow_completed_yields_operational_note(self) -> None:
        task = _task("open notepad then focus it")
        task.trace.append({
            "event": "workflow.completed",
            "workflow": {
                "patternId": "wf.open_and_focus",
                "steps": [
                    {"capability": "app.launch"},
                    {"capability": "app.focus"},
                ],
            },
        })
        proposed = self.reflector.reflect_on_task(task)
        self.assertEqual(len(proposed), 1)
        self.assertEqual(proposed[0]["kind"], "operational")
        self.assertIn("wf.open_and_focus", proposed[0]["summary"])

    def test_planner_clarification_yields_lesson(self) -> None:
        task = _task("write the file somewhere")
        task.trace.append({
            "event": "plan.evaluated",
            "plan": {
                "status": "clarification_needed",
                "matchedRule": "write.outside_sandbox",
                "ambiguity": "target must be under runtime/sandbox/",
            },
        })
        proposed = self.reflector.reflect_on_task(task)
        self.assertEqual(len(proposed), 1)
        self.assertEqual(proposed[0]["kind"], "lesson")
        self.assertIn("write.outside_sandbox", proposed[0]["summary"])

    def test_explicit_preference_yields_profile_note(self) -> None:
        task = _task("I prefer https when no scheme is given")
        proposed = self.reflector.reflect_on_task(task)
        kinds = [p["kind"] for p in proposed]
        self.assertIn("profile", kinds)

    def test_preference_with_sensitive_verb_skipped(self) -> None:
        # "always store the clipboard contents" must NOT become a profile
        # memory — clipboard content is a sensitive-data verb.
        task = _task("always store the clipboard contents")
        proposed = self.reflector.reflect_on_task(task)
        self.assertEqual(proposed, [])

    def test_dedup_within_task(self) -> None:
        task = _task("read")
        # Two identical failures of the same capability + error_type
        # should produce a single tool note.
        task.trace.append(_action_executed("filesystem.read", "failed",
                                           output={"error_type": "ScopeError"}))
        task.trace.append(_action_executed("filesystem.read", "failed",
                                           output={"error_type": "ScopeError"}))
        proposed = self.reflector.reflect_on_task(task)
        self.assertEqual(len(proposed), 1)


# ---------------------------------------------------------------------------
# Memory hints to the planner
# ---------------------------------------------------------------------------


class ApprovedMemoryHintsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(__file__).resolve().parents[3] / "runtime" / f"mem-{uuid4()}"
        self.store = _empty_store(self.tmp)
        # Two approved memories — one profile, one tool note for a
        # specific capability.
        self.profile_id = self.store.propose(MemoryItem(
            kind="profile", summary="user prefers https when scheme missing",
            details={}, evidence=[], trust_score=0.7,
        )).memory_id
        self.tool_id = self.store.propose(MemoryItem(
            kind="tool",
            summary="filesystem.write fails when path is outside sandbox",
            details={"capability": "filesystem.write"},
            evidence=[], trust_score=0.6,
        )).memory_id
        self.store.approve(self.profile_id)
        self.store.approve(self.tool_id)
        self.hints = ApprovedMemoryHints(self.store)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_profile_always_surfaces(self) -> None:
        out = self.hints.hints_for(capability="browser.navigate")
        kinds = [h["kind"] for h in out]
        self.assertIn("profile", kinds)

    def test_tool_hint_only_surfaces_for_matching_capability(self) -> None:
        out_match = self.hints.hints_for(capability="filesystem.write")
        out_other = self.hints.hints_for(capability="browser.navigate")
        # match: tool hint included, plus the always-on profile
        self.assertEqual(len({h["kind"] for h in out_match}), 2)
        # other: only profile, no tool
        self.assertEqual([h["kind"] for h in out_other], ["profile"])

    def test_candidates_never_surface(self) -> None:
        # Add a candidate (un-approved) tool note — must not appear.
        self.store.propose(MemoryItem(
            kind="tool",
            summary="candidate tool note",
            details={"capability": "filesystem.write"},
            evidence=[], trust_score=0.5,
        ))
        out = self.hints.hints_for(capability="filesystem.write")
        ids = {h["memoryId"] for h in out}
        self.assertNotIn("memory:candidate", ids)
        # only the two pre-approved memories show up
        self.assertEqual(len(out), 2)


# ---------------------------------------------------------------------------
# Memory must NOT bypass PolicyEngine
# ---------------------------------------------------------------------------


class MemoryDoesNotBypassPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(__file__).resolve().parents[3]
        self.root = self.workspace / "runtime" / f"mem-policy-{uuid4()}"
        (self.root / "configs").mkdir(parents=True, exist_ok=True)
        (self.root / "configs" / "policy.default.json").write_text(
            (self.workspace / "configs" / "policy.default.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.api = LocalSupervisorAPI(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_approved_memory_does_not_downgrade_tier_2(self) -> None:
        # Pre-approve a memory that *says* filesystem.move is fine.
        # That MUST NOT change the policy decision.
        item = self.api.memory.propose(MemoryItem(
            kind="profile",
            summary="user prefers filesystem.move without approval",
            details={"capability": "filesystem.move"},
            evidence=["user-said"], trust_score=0.95,
        ))
        self.api.memory.approve(item.memory_id)

        async def run():
            return await self.api.submit_voice_or_text_task("test policy guard")
        task = asyncio.run(run())
        proposal = ActionProposal(
            task_id=task.task_id,
            capability="filesystem.move",
            intent="move file",
            parameters={"src": "configs/policy.default.json",
                         "dst": "runtime/sandbox/policy.json"},
            requested_by="tester",
            evidence=["test"],
            confidence=0.99,
        )
        outcome = self.api.supervisor.propose_action(proposal)
        # filesystem.move is Tier 2 — policy must still queue an approval.
        self.assertEqual(outcome["status"], "awaiting_approval")
        self.assertIsNotNone(outcome.get("approval"))


# ---------------------------------------------------------------------------
# Bridge endpoints
# ---------------------------------------------------------------------------


def _http_post(host: str, port: int, path: str, body: dict | None = None):
    conn = HTTPConnection(host, port, timeout=5)
    conn.request("POST", path,
                 body=json.dumps(body or {}),
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    return resp.status, json.loads(resp.read().decode("utf-8") or "{}")


def _http_get(host: str, port: int, path: str):
    conn = HTTPConnection(host, port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    return resp.status, json.loads(resp.read().decode("utf-8") or "{}")


class MemoryBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(__file__).resolve().parents[3]
        self.root = self.workspace / "runtime" / f"mem-br-{uuid4()}"
        (self.root / "configs").mkdir(parents=True, exist_ok=True)
        (self.root / "configs" / "policy.default.json").write_text(
            (self.workspace / "configs" / "policy.default.json").read_text(encoding="utf-8"),
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

    def _seed(self, kind: str = "lesson") -> str:
        item = self.api.memory.propose(MemoryItem(
            kind=kind, summary="seeded memory", details={},
            evidence=["seed"], trust_score=0.5,
        ))
        return item.memory_id

    def test_get_memory_with_status_filter(self) -> None:
        a = self._seed()
        self._seed()  # second candidate
        self.api.memory.approve(a)
        code, body = _http_get(self.host, self.port,
                               "/memory?status=candidate")
        self.assertEqual(code, 200)
        ids = [m["memory_id"] for m in body["items"]]
        self.assertNotIn(a, ids)
        self.assertEqual(len(ids), 1)

    def test_proposals_endpoint_returns_only_candidates(self) -> None:
        a = self._seed()
        self.api.memory.approve(a)
        b = self._seed()
        code, body = _http_get(self.host, self.port, "/memory/proposals")
        self.assertEqual(code, 200)
        ids = [m["memory_id"] for m in body["items"]]
        self.assertEqual(ids, [b])

    def test_approve_endpoint_flips_status(self) -> None:
        mid = self._seed()
        code, body = _http_post(self.host, self.port,
                                f"/memory/{mid}/approve", {})
        self.assertEqual(code, 200)
        self.assertEqual(body["memory"]["status"], STATUS_APPROVED)

    def test_reject_endpoint_records_reason(self) -> None:
        mid = self._seed()
        code, body = _http_post(self.host, self.port,
                                f"/memory/{mid}/reject",
                                {"reason": "not actionable"})
        self.assertEqual(code, 200)
        self.assertEqual(body["memory"]["status"], STATUS_REJECTED)
        self.assertEqual(body["memory"]["review_reason"], "not actionable")

    def test_expire_endpoint_flips_status(self) -> None:
        mid = self._seed()
        self.api.memory.approve(mid)
        code, body = _http_post(self.host, self.port,
                                f"/memory/{mid}/expire", {})
        self.assertEqual(code, 200)
        self.assertEqual(body["memory"]["status"], STATUS_EXPIRED)

    def test_unknown_id_returns_404(self) -> None:
        code, _ = _http_post(self.host, self.port,
                             "/memory/memory:does-not-exist/approve", {})
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()
