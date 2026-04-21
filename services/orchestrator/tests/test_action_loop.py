"""End-to-end bridge tests for the structured action loop.

Covers:
 - Tier 0 (filesystem.list) auto-executes via /actions/propose.
 - Tier 2 (filesystem.move) returns awaiting_approval, then /actions/execute
   runs it and /hud-state reflects the latest result.
 - /actions/deny emits an approval.denied trace event and clears the approval.
 - Blocked patterns stay blocked even when an approval-bearing flow is used.
"""

from __future__ import annotations

import json
import shutil
import socket
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from src.jarvis_core import bridge
from src.jarvis_core.api import LocalSupervisorAPI


def _pick_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _http_get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def _http_post(url: str, payload: dict) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


class ActionLoopTests(unittest.TestCase):
    """Covers /actions/propose, /actions/execute, /actions/deny over HTTP."""

    def setUp(self) -> None:
        workspace_root = Path(__file__).resolve().parents[3]
        self.root = workspace_root / "runtime" / f"action-loop-test-{uuid4()}"
        (self.root / "configs").mkdir(parents=True, exist_ok=True)
        source_config = workspace_root / "configs" / "policy.default.json"
        (self.root / "configs" / "policy.default.json").write_text(
            source_config.read_text(encoding="utf-8"), encoding="utf-8"
        )
        # Prime sandbox so filesystem.move has something real to target.
        sandbox = self.root / "runtime" / "sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)
        (sandbox / "source.txt").write_text("hello", encoding="utf-8")

        self.api = LocalSupervisorAPI(self.root)
        self.port = _pick_port()
        self.server = bridge.start_server(self.api, port=self.port)
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    # --- Tier 0: auto-execute -------------------------------------------------

    def test_tier0_filesystem_list_auto_executes(self) -> None:
        configs_path = str(self.root / "configs")
        code, outcome = _http_post(f"{self.base}/actions/propose", {
            "capability": "filesystem.list",
            "parameters": {"path": configs_path},
            "intent": "List configs",
            "confidence": 0.95,
        })
        self.assertEqual(code, 201)
        self.assertEqual(outcome["status"], "executed")
        self.assertEqual(outcome["decision"]["risk_tier"], 0)
        self.assertIn("result", outcome)
        self.assertIn("action_id", outcome)

        # /actions/{id} returns the persisted result.
        code, fetched = _http_get(f"{self.base}/actions/{outcome['action_id']}")
        self.assertEqual(code, 200)
        self.assertEqual(fetched["status"], "executed")

    # --- Tier 2: approve flow -------------------------------------------------

    def test_tier2_move_requires_approval_then_executes(self) -> None:
        code, outcome = _http_post(f"{self.base}/actions/propose", {
            "capability": "filesystem.move",
            "parameters": {
                "source": str(self.root / "runtime" / "sandbox" / "source.txt"),
                "destination": str(self.root / "runtime" / "sandbox" / "dest.txt"),
            },
            "intent": "Move a sandbox file",
            "confidence": 0.9,
        })
        self.assertEqual(code, 201)
        self.assertEqual(outcome["status"], "awaiting_approval")
        self.assertEqual(outcome["decision"]["risk_tier"], 2)
        approval_id = outcome["approval"]["approval_id"]

        # Approvals endpoint lists the queued request.
        code, body = _http_get(f"{self.base}/approvals")
        self.assertEqual(code, 200)
        self.assertTrue(any(a["approval_id"] == approval_id for a in body["items"]))

        # Execute it.
        code, exec_body = _http_post(f"{self.base}/actions/execute",
                                     {"approval_id": approval_id})
        self.assertEqual(code, 200)
        self.assertEqual(exec_body["result"]["status"], "executed")

        # /hud-state surfaces the latest result.
        code, state = _http_get(f"{self.base}/hud-state")
        self.assertEqual(code, 200)
        self.assertIsNotNone(state["latestResult"])
        self.assertEqual(state["latestResult"]["status"], "executed")
        self.assertEqual(state["latestResult"]["capability"], "filesystem.move")

        # Approval is consumed.
        code, body = _http_get(f"{self.base}/approvals")
        self.assertFalse(any(a["approval_id"] == approval_id for a in body["items"]))

    # --- Deny flow ------------------------------------------------------------

    def test_deny_emits_trace_event_and_clears_approval(self) -> None:
        _, outcome = _http_post(f"{self.base}/actions/propose", {
            "capability": "filesystem.move",
            "parameters": {
                "source": str(self.root / "runtime" / "sandbox" / "source.txt"),
                "destination": str(self.root / "runtime" / "sandbox" / "dest.txt"),
            },
        })
        approval_id = outcome["approval"]["approval_id"]

        code, body = _http_post(f"{self.base}/actions/deny",
                                {"approval_id": approval_id, "reason": "nope"})
        self.assertEqual(code, 200)
        self.assertEqual(body["approval_id"], approval_id)
        self.assertEqual(body["reason"], "nope")

        # Trace on the owning task includes approval.denied.
        code, trace_body = _http_get(
            f"{self.base}/tasks/{outcome['task_id']}/trace")
        self.assertEqual(code, 200)
        events = [e.get("event") for e in trace_body["trace"]]
        self.assertIn("approval.denied", events)

        # Approval is gone.
        code, body = _http_get(f"{self.base}/approvals")
        self.assertFalse(any(a["approval_id"] == approval_id for a in body["items"]))

    def test_execute_unknown_approval_returns_404(self) -> None:
        code, _ = _http_post(f"{self.base}/actions/execute",
                             {"approval_id": "no-such-thing"})
        self.assertEqual(code, 404)

    def test_propose_rejects_missing_capability(self) -> None:
        code, body = _http_post(f"{self.base}/actions/propose", {"parameters": {}})
        self.assertEqual(code, 400)
        self.assertIn("error", body)

    # --- Blocked pattern: Tier 3 stays blocked --------------------------------

    def test_blocked_pattern_stays_blocked_even_when_proposed(self) -> None:
        code, outcome = _http_post(f"{self.base}/actions/propose", {
            "capability": "system.delete",
            "parameters": {"target": "format disk"},
            "intent": "format disk",
            "confidence": 0.99,
        })
        self.assertEqual(code, 201)
        # Tier 3 → policy blocks regardless; gateway raises approval then
        # execute() returns blocked.  The supervisor flow reports that via
        # status == "blocked" (or awaiting_approval with blocked decision).
        self.assertIn(outcome["status"], ("blocked", "awaiting_approval"))
        self.assertEqual(outcome["decision"]["risk_tier"], 3)
        self.assertTrue(outcome["decision"]["blocked"])


if __name__ == "__main__":
    unittest.main()
