from __future__ import annotations

import json
import shutil
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from uuid import uuid4

from src.jarvis_core import bridge
from src.jarvis_core.api import LocalSupervisorAPI


def _http_get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def _http_post(url: str, payload: dict) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


class BridgeTests(unittest.TestCase):
    """Tests the local HUD bridge server end-to-end over HTTP."""

    port = 0  # assigned per-run to avoid conflicts

    def setUp(self) -> None:
        workspace_root = Path(__file__).resolve().parents[3]
        self.root = workspace_root / "runtime" / f"bridge-test-{uuid4()}"
        (self.root / "configs").mkdir(parents=True, exist_ok=True)
        source_config = workspace_root / "configs" / "policy.default.json"
        (self.root / "configs" / "policy.default.json").write_text(
            source_config.read_text(encoding="utf-8"), encoding="utf-8"
        )
        self.api = LocalSupervisorAPI(self.root)

        # Ephemeral port: 0 → OS picks. Use a random high port in range.
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        BridgeTests.port = s.getsockname()[1]
        s.close()

        self.server = bridge.start_server(self.api, port=BridgeTests.port)
        self.base = f"http://127.0.0.1:{BridgeTests.port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def test_health_endpoint(self) -> None:
        code, body = _http_get(f"{self.base}/health")
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "ok")

    def test_hud_state_initially_empty(self) -> None:
        code, body = _http_get(f"{self.base}/hud-state")
        self.assertEqual(code, 200)
        self.assertEqual(body["mode"], "Guarded Autonomy")
        self.assertEqual(body["task"], "")
        self.assertEqual(len(body["agents"]), 4)
        self.assertFalse(body["degraded"])

    def test_submit_task_updates_hud_state(self) -> None:
        code, task = _http_post(f"{self.base}/tasks",
                                {"objective": "Review the installer page."})
        self.assertEqual(code, 201)
        self.assertIn("task_id", task)
        self.assertEqual(task["objective"], "Review the installer page.")

        code, state = _http_get(f"{self.base}/hud-state")
        self.assertEqual(code, 200)
        self.assertEqual(state["task"], "Review the installer page.")
        self.assertGreater(len(state["trace"]), 0)

    def test_submit_task_rejects_empty_objective(self) -> None:
        code, body = _http_post(f"{self.base}/tasks", {"objective": "   "})
        self.assertEqual(code, 400)
        self.assertIn("error", body)

    def test_fetch_trace_for_submitted_task(self) -> None:
        _, task = _http_post(f"{self.base}/tasks", {"objective": "Read a page."})
        code, body = _http_get(f"{self.base}/tasks/{task['task_id']}/trace")
        self.assertEqual(code, 200)
        self.assertEqual(body["task_id"], task["task_id"])
        self.assertIsInstance(body["trace"], list)

    def test_fetch_trace_unknown_task_returns_404(self) -> None:
        code, _ = _http_get(f"{self.base}/tasks/does-not-exist/trace")
        self.assertEqual(code, 404)

    def test_memory_endpoint_returns_items_list(self) -> None:
        code, body = _http_get(f"{self.base}/memory")
        self.assertEqual(code, 200)
        self.assertIn("items", body)
        self.assertIsInstance(body["items"], list)


if __name__ == "__main__":
    unittest.main()
