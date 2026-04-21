"""Tests for the in-process BrowserContext and the new browser capabilities.

Covers:
 - BrowserContext snapshot / record / clear semantics.
 - browser.read_page now populates the context and returns a text excerpt.
 - browser.summarize fetches when given a url and reuses context otherwise.
 - browser.current_page returns the stored context (or fails clearly).
 - Planner routes 'read this page' / 'summarize this page' / 'what page am I on'
   based on whether a browser context is available.
 - 'open <URL> and read it' maps to browser.read_page.
"""

from __future__ import annotations

import asyncio
import shutil
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from uuid import uuid4

from src.jarvis_core.api import LocalSupervisorAPI
from src.jarvis_core.browser_context import BrowserContext
from src.jarvis_core.models import ActionProposal
from src.jarvis_core.planner import (
    CLARIFICATION_NEEDED,
    MAPPED,
    DeterministicPlanner,
)


# ---------------------------------------------------------------------------
# BrowserContext unit tests
# ---------------------------------------------------------------------------

class BrowserContextTests(unittest.TestCase):
    def test_empty_context_returns_none_and_flag(self) -> None:
        ctx = BrowserContext()
        self.assertFalse(ctx.has_context())
        self.assertIsNone(ctx.snapshot())

    def test_record_and_snapshot_round_trip(self) -> None:
        ctx = BrowserContext()
        snap = ctx.record_page(
            url="https://example.com",
            title="Example",
            text_excerpt="Hello world.",
            byte_count=123,
            source="browser.read_page",
        )
        self.assertTrue(ctx.has_context())
        self.assertEqual(snap["url"], "https://example.com")
        self.assertEqual(snap["title"], "Example")
        self.assertEqual(snap["byteCount"], 123)
        self.assertEqual(snap["source"], "browser.read_page")
        self.assertIsNotNone(snap["updatedAt"])

    def test_record_requires_url(self) -> None:
        ctx = BrowserContext()
        with self.assertRaises(ValueError):
            ctx.record_page(url="", title="x")

    def test_clear_empties_context(self) -> None:
        ctx = BrowserContext()
        ctx.record_page(url="https://a.example", title="a")
        ctx.clear()
        self.assertFalse(ctx.has_context())
        self.assertIsNone(ctx.snapshot())


# ---------------------------------------------------------------------------
# Capability integration (loopback HTTP server, no external traffic)
# ---------------------------------------------------------------------------

class _PageHandler(BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):  # silence
        pass

    def do_GET(self):
        body = (
            b"<html><head><title>Guarded Assistant Notes</title></head><body>"
            b"<p>First sentence about Jarvis. Second sentence is also here. "
            b"Third sentence closes the demo.</p>"
            b"<script>alert('ignored');</script>"
            b"</body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class BrowserCapabilityContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = HTTPServer(("127.0.0.1", 0), _PageHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        workspace_root = Path(__file__).resolve().parents[3]
        self.root = workspace_root / "runtime" / f"browser-ctx-{uuid4()}"
        (self.root / "configs").mkdir(parents=True, exist_ok=True)
        (self.root / "configs" / "policy.default.json").write_text(
            (workspace_root / "configs" / "policy.default.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.api = LocalSupervisorAPI(self.root)
        self.task = asyncio.run(self.api.submit_voice_or_text_task("browser ctx test"))
        self.url = f"http://127.0.0.1:{self.port}/"

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def _propose(self, capability: str, parameters: dict) -> ActionProposal:
        return ActionProposal(
            task_id=self.task.task_id,
            capability=capability,
            intent="browser ctx test",
            parameters=parameters,
            requested_by="test",
            evidence=["unit test"],
            confidence=0.95,
        )

    def test_read_page_populates_context_and_extracts_text(self) -> None:
        result = self.api.submit_action(self._propose("browser.read_page", {"url": self.url}))
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.output["title"], "Guarded Assistant Notes")
        self.assertIn("First sentence", result.output["text_excerpt"])
        self.assertNotIn("<script", result.output["text_excerpt"])
        self.assertNotIn("alert(", result.output["text_excerpt"])
        snap = self.api.browser_context.snapshot()
        self.assertIsNotNone(snap)
        self.assertEqual(snap["url"], self.url)
        self.assertEqual(snap["source"], "browser.read_page")

    def test_summarize_url_returns_sentences(self) -> None:
        result = self.api.submit_action(
            self._propose("browser.summarize", {"url": self.url})
        )
        self.assertEqual(result.status, "executed")
        self.assertGreaterEqual(len(result.output["summary_sentences"]), 1)
        self.assertEqual(result.output["source"], "fetch")

    def test_summarize_from_context_reuses_last_read(self) -> None:
        # Seed the context through a real read, then summarize without a URL.
        self.api.submit_action(self._propose("browser.read_page", {"url": self.url}))
        result = self.api.submit_action(
            self._propose("browser.summarize", {"use_context": True})
        )
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.output["source"], "context")
        self.assertEqual(result.output["url"], self.url)

    def test_summarize_without_url_or_context_fails_clearly(self) -> None:
        result = self.api.submit_action(
            self._propose("browser.summarize", {"use_context": True})
        )
        self.assertEqual(result.status, "failed")
        self.assertIn("No browser context", result.output["error"])

    def test_current_page_without_context_fails_clearly(self) -> None:
        result = self.api.submit_action(self._propose("browser.current_page", {}))
        self.assertEqual(result.status, "failed")
        self.assertIn("No browser context", result.output["error"])

    def test_current_page_returns_stored_context(self) -> None:
        self.api.submit_action(self._propose("browser.read_page", {"url": self.url}))
        result = self.api.submit_action(self._propose("browser.current_page", {}))
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.output["url"], self.url)
        self.assertEqual(result.output["title"], "Guarded Assistant Notes")


# ---------------------------------------------------------------------------
# Planner routing for browser-context intents
# ---------------------------------------------------------------------------

class PlannerBrowserContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = DeterministicPlanner()

    def test_summarize_url_maps(self) -> None:
        r = self.planner.plan("summarize https://example.com")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "browser.summarize")
        self.assertEqual(r.parameters, {"url": "https://example.com"})

    def test_summarize_this_page_without_context_clarifies(self) -> None:
        r = self.planner.plan("summarize this page")
        self.assertEqual(r.status, CLARIFICATION_NEEDED)
        self.assertEqual(r.matched_rule, "summarize.no_context")

    def test_summarize_this_page_with_context_maps(self) -> None:
        r = self.planner.plan("summarize this page", has_browser_context=True)
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "browser.summarize")
        self.assertTrue(r.parameters.get("use_context"))

    def test_what_page_am_i_on_without_context(self) -> None:
        r = self.planner.plan("what page am I on?")
        self.assertEqual(r.status, CLARIFICATION_NEEDED)
        self.assertEqual(r.matched_rule, "current_page.no_context")

    def test_what_page_am_i_on_with_context(self) -> None:
        r = self.planner.plan("what page am I on?", has_browser_context=True)
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "browser.current_page")

    def test_which_page_is_open_variant(self) -> None:
        r = self.planner.plan("which page is open?", has_browser_context=True)
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "browser.current_page")

    def test_open_and_read_maps_to_read_page(self) -> None:
        r = self.planner.plan("open https://example.com and read it")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "browser.read_page")
        self.assertEqual(r.parameters["url"], "https://example.com")

    def test_open_and_summarize_maps_to_summarize(self) -> None:
        r = self.planner.plan("open https://example.com and summarize it")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "browser.summarize")

    def test_unsupported_browser_action_stays_unsupported(self) -> None:
        # We deliberately do not map click/fill/submit here.
        r = self.planner.plan("click the submit button on example.com")
        self.assertEqual(r.status, "unsupported")


# ---------------------------------------------------------------------------
# End-to-end: planner + real adapter on loopback
# ---------------------------------------------------------------------------

class PlannerBrowserEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = HTTPServer(("127.0.0.1", 0), _PageHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        workspace_root = Path(__file__).resolve().parents[3]
        self.root = workspace_root / "runtime" / f"browser-e2e-{uuid4()}"
        (self.root / "configs").mkdir(parents=True, exist_ok=True)
        (self.root / "configs" / "policy.default.json").write_text(
            (workspace_root / "configs" / "policy.default.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.api = LocalSupervisorAPI(self.root)
        self.url = f"http://127.0.0.1:{self.port}/"

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def test_read_url_then_summarize_this_page(self) -> None:
        # First task: explicit read of a URL. The deterministic planner
        # routes this through browser.read_page and populates context.
        task1 = asyncio.run(self.api.submit_voice_or_text_task(f"read {self.url}"))
        self.assertEqual(task1.context["plan"]["capability"], "browser.read_page")
        self.assertEqual(task1.context["planAction"]["status"], "executed")
        self.assertTrue(self.api.browser_context.has_context())

        # Second task: 'summarize this page' — now context is available,
        # so the planner should route to browser.summarize(use_context).
        task2 = asyncio.run(self.api.submit_voice_or_text_task("summarize this page"))
        self.assertEqual(task2.context["plan"]["capability"], "browser.summarize")
        self.assertEqual(task2.context["planAction"]["status"], "executed")

    def test_read_this_page_without_context_clarifies(self) -> None:
        task = asyncio.run(self.api.submit_voice_or_text_task("read this page"))
        self.assertEqual(task.context["plan"]["status"], "clarification_needed")
        self.assertNotIn("planAction", task.context)


if __name__ == "__main__":
    unittest.main()
