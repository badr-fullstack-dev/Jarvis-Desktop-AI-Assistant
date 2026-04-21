"""Tests for real capability adapters (browser/filesystem/applications).

All tests go through LocalSupervisorAPI + ActionGateway so we exercise the
policy engine too.  No external network or subprocess runs in these tests —
browser is tested in dry-run and via a loopback HTTPServer; app.launch is
tested in dry-run mode only to keep CI side-effect-free.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from uuid import uuid4

from src.jarvis_core.api import LocalSupervisorAPI
from src.jarvis_core.capabilities.applications import ApplicationCapability
from src.jarvis_core.capabilities.browser import BrowserCapability
from src.jarvis_core.capabilities.filesystem import FilesystemCapability, ScopeError
from src.jarvis_core.models import ActionProposal


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class _CapabilityTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace_root = Path(__file__).resolve().parents[3]
        self.root = self.workspace_root / "runtime" / f"cap-test-{uuid4()}"
        (self.root / "configs").mkdir(parents=True, exist_ok=True)
        (self.root / "configs" / "policy.default.json").write_text(
            (self.workspace_root / "configs" / "policy.default.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.api = LocalSupervisorAPI(self.root)
        self.task = asyncio.run(self.api.submit_voice_or_text_task("capability test bed"))

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def _proposal(self, capability: str, parameters: dict, *, confidence: float = 0.95,
                  dry_run: bool = False, intent: str = "test") -> ActionProposal:
        return ActionProposal(
            task_id=self.task.task_id,
            capability=capability,
            intent=intent,
            parameters=parameters,
            requested_by="test",
            evidence=["unit test"],
            confidence=confidence,
            dry_run=dry_run,
        )


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------

class _TitlePageHandler(BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):  # silence
        pass

    def do_GET(self):
        body = b"<html><head><title>Hello Jarvis</title></head><body>ok</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class BrowserCapabilityTests(_CapabilityTestBase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = HTTPServer(("127.0.0.1", 0), _TitlePageHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_read_page_returns_title_and_status(self) -> None:
        prop = self._proposal("browser.read_page", {"url": f"http://127.0.0.1:{self.port}/"})
        result = self.api.submit_action(prop, approved=False)
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.output["title"], "Hello Jarvis")
        self.assertEqual(result.output["status"], 200)
        self.assertTrue(result.verification["ok"])

    def test_read_page_rejects_non_http_url(self) -> None:
        prop = self._proposal("browser.read_page", {"url": "file:///etc/passwd"})
        result = self.api.submit_action(prop, approved=False)
        self.assertEqual(result.status, "failed")
        self.assertIn("http/https", result.output["error"])

    def test_navigate_dry_run_does_not_open_browser(self) -> None:
        prop = self._proposal("browser.navigate", {"url": "https://example.com"}, dry_run=True)
        # Tier 0 with dry_run=True is actually tier_0 → no approval needed.
        result = self.api.submit_action(prop, approved=False)
        self.assertEqual(result.status, "executed")
        self.assertFalse(result.output["opened"])
        self.assertTrue(result.output["dry_run"])

    def test_download_requires_approval_tier2(self) -> None:
        prop = self._proposal("browser.download_file",
                              {"url": f"http://127.0.0.1:{self.port}/", "filename": "x.html"})
        result = self.api.submit_action(prop, approved=False)
        self.assertEqual(result.status, "awaiting_approval")

    def test_download_rejects_filename_with_path_separator(self) -> None:
        adapter = BrowserCapability(sandbox_root=self.root / "runtime" / "sandbox")
        prop = self._proposal("browser.download_file",
                              {"url": f"http://127.0.0.1:{self.port}/", "filename": "../evil"})
        result = adapter.execute(prop)
        self.assertEqual(result.status, "failed")


# ---------------------------------------------------------------------------
# Filesystem
# ---------------------------------------------------------------------------

class FilesystemCapabilityTests(_CapabilityTestBase):
    def test_read_within_workspace_is_tier_zero_and_allowed(self) -> None:
        # The copied policy file lives under self.root (the API's workspace_root).
        target = self.root / "configs" / "policy.default.json"
        prop = self._proposal("filesystem.read", {"path": str(target)})
        result = self.api.submit_action(prop, approved=False)
        self.assertEqual(result.status, "executed")
        self.assertGreater(result.output["size"], 0)

    def test_read_outside_scope_fails_with_scope_error(self) -> None:
        prop = self._proposal("filesystem.read", {"path": r"C:\Windows\System32\drivers\etc\hosts"})
        result = self.api.submit_action(prop, approved=False)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.output["error_type"], "ScopeError")

    def test_read_missing_path_fails_cleanly(self) -> None:
        prop = self._proposal("filesystem.read",
                              {"path": str(self.root / "nonexistent.txt")})
        result = self.api.submit_action(prop, approved=False)
        self.assertEqual(result.status, "failed")

    def test_list_directory(self) -> None:
        prop = self._proposal("filesystem.list", {"path": str(self.root / "configs")})
        result = self.api.submit_action(prop, approved=False)
        self.assertEqual(result.status, "executed")
        self.assertGreaterEqual(result.output["count"], 1)

    def test_search_finds_known_file(self) -> None:
        prop = self._proposal("filesystem.search",
                              {"path": str(self.root / "configs"),
                               "pattern": "*.json"})
        result = self.api.submit_action(prop, approved=False)
        self.assertEqual(result.status, "executed")
        self.assertTrue(any("policy.default.json" in m for m in result.output["matches"]))

    def test_write_requires_sandbox_scope(self) -> None:
        # Writing outside the sandbox (in workspace root) must fail with ScopeError.
        prop = self._proposal("filesystem.write",
                              {"path": str(self.workspace_root / "ILLEGAL.txt"),
                               "content": "nope"})
        result = self.api.submit_action(prop, approved=False)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.output["error_type"], "ScopeError")

    def test_write_inside_sandbox_succeeds_and_verifies(self) -> None:
        sandbox = self.root / "runtime" / "sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)
        target = sandbox / "hello.txt"
        prop = self._proposal("filesystem.write",
                              {"path": str(target), "content": "hello"},
                              confidence=0.99)
        result = self.api.submit_action(prop, approved=False)
        self.assertEqual(result.status, "executed")
        self.assertTrue(target.exists())
        self.assertTrue(result.verification["file_exists"])

    def test_move_is_tier2_requires_approval(self) -> None:
        # Set up a file inside the sandbox so the source is in-scope.
        sandbox = self.root / "runtime" / "sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)
        src = sandbox / "src.txt"
        src.write_text("x", encoding="utf-8")
        prop = self._proposal("filesystem.move",
                              {"source": str(src), "destination": str(sandbox / "dst.txt")})
        result = self.api.submit_action(prop, approved=False)
        self.assertEqual(result.status, "awaiting_approval")

    def test_move_executes_when_approved(self) -> None:
        sandbox = self.root / "runtime" / "sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)
        src = sandbox / "src.txt"
        src.write_text("x", encoding="utf-8")
        dst = sandbox / "dst.txt"
        prop = self._proposal("filesystem.move",
                              {"source": str(src), "destination": str(dst)})
        result = self.api.submit_action(prop, approved=True)
        self.assertEqual(result.status, "executed")
        self.assertFalse(src.exists())
        self.assertTrue(dst.exists())

    def test_write_dry_run_does_not_touch_disk(self) -> None:
        sandbox = self.root / "runtime" / "sandbox"
        target = sandbox / "dryrun.txt"
        # dry_run=True flips tier_1 to requires_approval; use approved=True to force exec path.
        prop = self._proposal("filesystem.write",
                              {"path": str(target), "content": "nope"},
                              confidence=0.99, dry_run=True)
        result = self.api.submit_action(prop, approved=True)
        self.assertEqual(result.status, "executed")
        self.assertFalse(target.exists())


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

class ApplicationCapabilityTests(_CapabilityTestBase):
    def test_launch_dry_run_resolves_allowlist_without_spawning(self) -> None:
        prop = self._proposal("app.launch", {"name": "notepad"}, dry_run=True)
        result = self.api.submit_action(prop, approved=True)
        # dry_run sets the tier_1 to require approval; approved=True lets it run.
        self.assertEqual(result.status, "executed")
        self.assertTrue(result.output["dry_run"])
        self.assertIsNone(result.output["pid"])

    def test_launch_rejects_unknown_application(self) -> None:
        prop = self._proposal("app.launch", {"name": "definitely-not-a-real-app"},
                              confidence=0.99)
        result = self.api.submit_action(prop, approved=False)
        self.assertEqual(result.status, "failed")
        self.assertIn("allowlist", result.output["error"].lower())

    def test_launch_rejects_args_with_non_string_values(self) -> None:
        adapter = ApplicationCapability()
        prop = self._proposal("app.launch",
                              {"name": "notepad", "args": [123]},
                              confidence=0.99)
        result = adapter.execute(prop)
        self.assertEqual(result.status, "failed")

    def test_install_is_tier_two_and_fails_cleanly_after_approval(self) -> None:
        prop = self._proposal("app.install", {"name": "some-package"})
        pending = self.api.submit_action(prop, approved=False)
        self.assertEqual(pending.status, "awaiting_approval")

        approved = self.api.submit_action(prop, approved=True)
        self.assertEqual(approved.status, "failed")
        self.assertEqual(approved.output["error"], "not_implemented")

    def test_focus_routes_like_launch(self) -> None:
        prop = self._proposal("app.focus", {"name": "notepad"}, dry_run=True)
        result = self.api.submit_action(prop, approved=True)
        self.assertEqual(result.status, "executed")


# ---------------------------------------------------------------------------
# Adapter-level scope unit tests (no gateway)
# ---------------------------------------------------------------------------

class FilesystemScopeTests(unittest.TestCase):
    def test_scope_error_when_no_roots_configured(self) -> None:
        adapter = FilesystemCapability()
        prop = ActionProposal(task_id="t", capability="filesystem.read",
                              intent="i", parameters={"path": "anything"},
                              requested_by="r", evidence=[])
        result = adapter.execute(prop)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.output["error_type"], "ScopeError")


if __name__ == "__main__":
    unittest.main()
