"""Tests for the local OCR layer.

Covers:

* OCR provider abstraction — UnavailableOCRProvider, CompositeOCRProvider,
  build_ocr_provider_from_env.
* DesktopCapability OCR adapters — desktop.ocr_foreground / _full /
  _screenshot — using injected fakes (no real winsdk import, no real
  capture). Dry-run, non-Windows, missing provider, missing screenshot,
  path traversal, truncation, line cap.
* Planner mappings for every supported phrasing.
* End-to-end via LocalSupervisorAPI: "ocr my current window" auto-plans
  and routes through ActionGateway.
* Bridge desktop view — `latestOcr` is wired up and surfaces text.
"""

from __future__ import annotations

import asyncio
import shutil
import unittest
from http.client import HTTPConnection
from pathlib import Path
from uuid import uuid4

from src.jarvis_core.api import LocalSupervisorAPI
from src.jarvis_core.bridge import _build_desktop_view, start_server
from src.jarvis_core.capabilities.desktop import (
    DesktopCapability,
    _MAX_OCR_LINES,
    _MAX_OCR_TEXT_BYTES,
    _rgb_to_png,
)
from src.jarvis_core.models import ActionProposal
from src.jarvis_core.ocr_providers import (
    CompositeOCRProvider,
    OCRError,
    OCRLine,
    OCRProvider,
    OCRResult,
    UnavailableOCRProvider,
    build_ocr_provider_from_env,
)
from src.jarvis_core.planner import (
    CLARIFICATION_NEEDED,
    MAPPED,
    UNSUPPORTED,
    DeterministicPlanner,
)


def _tiny_rgb(w: int, h: int, color=(10, 20, 30)) -> bytes:
    return bytes(color) * (w * h)


def _tiny_png() -> bytes:
    return _rgb_to_png(2, 2, _tiny_rgb(2, 2))


# ---------------------------------------------------------------------------
# Fake providers
# ---------------------------------------------------------------------------


class _FakeProvider(OCRProvider):
    """Returns a fixed text and line list. Records the last call."""

    name = "fake"

    def __init__(self, text: str = "hello world", lines=None,
                 average_confidence=None, language="en-US",
                 raise_error: OCRError | None = None) -> None:
        self._text = text
        self._lines = lines if lines is not None else [
            OCRLine(text="hello", confidence=None),
            OCRLine(text="world", confidence=None),
        ]
        self._avg = average_confidence
        self._lang = language
        self._raise = raise_error
        self.last_input: bytes | None = None

    def available(self) -> bool:
        return True

    def extract(self, png_bytes, *, language=None):
        self.last_input = png_bytes
        if self._raise is not None:
            raise self._raise
        return OCRResult(
            text=self._text,
            lines=list(self._lines),
            language=self._lang,
            average_confidence=self._avg,
            provider=self.name,
        )


# ---------------------------------------------------------------------------
# Provider builder + composite
# ---------------------------------------------------------------------------


class OCRProviderBuilderTests(unittest.TestCase):
    def test_default_is_unavailable(self) -> None:
        p = build_ocr_provider_from_env({})
        self.assertIsInstance(p, UnavailableOCRProvider)
        self.assertFalse(p.available())
        with self.assertRaises(OCRError):
            p.extract(b"\x89PNG")

    def test_explicit_unavailable_synonyms(self) -> None:
        for v in ("unavailable", "off", "disabled", "none", ""):
            p = build_ocr_provider_from_env({"JARVIS_OCR_PROVIDER": v})
            self.assertIsInstance(p, UnavailableOCRProvider)

    def test_unknown_value_raises(self) -> None:
        with self.assertRaises(ValueError):
            build_ocr_provider_from_env({"JARVIS_OCR_PROVIDER": "magic"})

    def test_auto_returns_composite(self) -> None:
        p = build_ocr_provider_from_env({"JARVIS_OCR_PROVIDER": "auto"})
        self.assertIsInstance(p, CompositeOCRProvider)
        self.assertIn("windows-media-ocr", p.name)
        self.assertIn("unavailable", p.name)

    def test_composite_falls_through_unavailable_to_real(self) -> None:
        fake = _FakeProvider(text="real text")
        comp = CompositeOCRProvider([UnavailableOCRProvider(), fake])
        self.assertTrue(comp.available())
        out = comp.extract(_tiny_png())
        self.assertEqual(out.text, "real text")

    def test_composite_raises_when_every_provider_fails(self) -> None:
        comp = CompositeOCRProvider([UnavailableOCRProvider(), UnavailableOCRProvider()])
        self.assertFalse(comp.available())
        with self.assertRaises(OCRError):
            comp.extract(_tiny_png())


# ---------------------------------------------------------------------------
# OCR capabilities
# ---------------------------------------------------------------------------


class OCRAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(__file__).resolve().parents[3] / "runtime" / f"ocr-{uuid4()}"
        self.tmp.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _adapter(self, *, provider=None, foreground=True, full=True) -> DesktopCapability:
        return DesktopCapability(
            capture_foreground_fn=(lambda: (2, 2, _tiny_rgb(2, 2, (200, 100, 50))))
            if foreground else None,
            capture_full_fn=(lambda: (3, 1, _tiny_rgb(3, 1, (0, 255, 0))))
            if full else None,
            screenshots_dir=self.tmp,
            platform="win32",
            ocr_provider=provider,
        )

    def _proposal(self, capability, **kwargs):
        params = kwargs.pop("parameters", {})
        return ActionProposal(
            task_id="t", capability=capability, intent="i",
            parameters=params, requested_by="test", evidence=[],
            **kwargs,
        )

    # --- foreground / full -------------------------------------------------

    def test_ocr_foreground_runs_provider_on_captured_png(self) -> None:
        provider = _FakeProvider(text="line 1\nline 2", lines=[
            OCRLine(text="line 1"), OCRLine(text="line 2"),
        ])
        adapter = self._adapter(provider=provider)
        result = adapter.execute(self._proposal("desktop.ocr_foreground"))
        self.assertEqual(result.status, "executed")
        out = result.output
        self.assertEqual(out["mode"], "foreground")
        self.assertEqual(out["text"], "line 1\nline 2")
        self.assertEqual(out["line_count"], 2)
        self.assertEqual(out["char_count"], len("line 1\nline 2"))
        self.assertEqual(len(out["lines"]), 2)
        self.assertFalse(out["truncated"])
        self.assertEqual(out["language"], "en-US")
        self.assertEqual(out["provider"], "fake")
        # The screenshot side-effect: a real PNG written to disk that
        # the HUD can preview alongside the OCR output.
        meta = out["screenshot"]
        self.assertTrue(meta["name"].startswith("screenshot-"))
        self.assertTrue(meta["name"].endswith(".png"))
        self.assertEqual(meta["width"], 2)
        self.assertEqual(meta["height"], 2)
        path = Path(meta["path"])
        self.assertTrue(path.is_file())
        self.assertTrue(path.read_bytes().startswith(b"\x89PNG"))
        # Provider must have been fed the PNG bytes (not the raw RGB).
        self.assertIsNotNone(provider.last_input)
        assert provider.last_input is not None  # type narrow
        self.assertTrue(provider.last_input.startswith(b"\x89PNG"))

    def test_ocr_full_uses_full_capture(self) -> None:
        provider = _FakeProvider(text="screen text")
        adapter = self._adapter(provider=provider)
        result = adapter.execute(self._proposal("desktop.ocr_full"))
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.output["mode"], "full")
        self.assertEqual(result.output["screenshot"]["width"], 3)
        self.assertEqual(result.output["screenshot"]["height"], 1)

    # --- ocr_screenshot ----------------------------------------------------

    def test_ocr_screenshot_reads_existing_png(self) -> None:
        # Plant a PNG with the canonical name shape.
        png = _tiny_png()
        name = "screenshot-existing-1.png"
        (self.tmp / name).write_bytes(png)
        provider = _FakeProvider(text="from disk")
        adapter = self._adapter(provider=provider)
        result = adapter.execute(self._proposal(
            "desktop.ocr_screenshot", parameters={"name": name},
        ))
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.output["mode"], "screenshot")
        self.assertEqual(result.output["text"], "from disk")
        self.assertEqual(result.output["screenshot"]["name"], name)
        self.assertEqual(provider.last_input, png)

    def test_ocr_screenshot_rejects_path_traversal(self) -> None:
        provider = _FakeProvider()
        adapter = self._adapter(provider=provider)
        for bad in [
            "../secret.png",
            "screenshot-../evil.png",
            "screenshot-x/y.png",
            "SCREENSHOT-x.png",
            "notes.txt",
        ]:
            result = adapter.execute(self._proposal(
                "desktop.ocr_screenshot", parameters={"name": bad},
            ))
            self.assertEqual(result.status, "failed", bad)
        # Provider must never have been called for any of the bad inputs.
        self.assertIsNone(provider.last_input)

    def test_ocr_screenshot_missing_file_fails_honestly(self) -> None:
        provider = _FakeProvider()
        adapter = self._adapter(provider=provider)
        result = adapter.execute(self._proposal(
            "desktop.ocr_screenshot",
            parameters={"name": "screenshot-not-here.png"},
        ))
        self.assertEqual(result.status, "failed")
        self.assertIn("not found", result.output["error"].lower())

    def test_ocr_screenshot_requires_name(self) -> None:
        provider = _FakeProvider()
        adapter = self._adapter(provider=provider)
        result = adapter.execute(self._proposal(
            "desktop.ocr_screenshot", parameters={},
        ))
        self.assertEqual(result.status, "failed")
        self.assertIn("name", result.output["error"].lower())

    # --- Provider failure paths -------------------------------------------

    def test_unavailable_provider_fails_with_remediation_hint(self) -> None:
        adapter = self._adapter(provider=UnavailableOCRProvider())
        result = adapter.execute(self._proposal("desktop.ocr_foreground"))
        self.assertEqual(result.status, "failed")
        self.assertIn("JARVIS_OCR_PROVIDER", result.output["error"])

    def test_provider_runtime_error_surfaces_as_failed_action(self) -> None:
        provider = _FakeProvider(raise_error=OCRError("model crashed"))
        adapter = self._adapter(provider=provider)
        result = adapter.execute(self._proposal("desktop.ocr_foreground"))
        self.assertEqual(result.status, "failed")
        self.assertIn("model crashed", result.output["error"])

    # --- Dry-run + platform guard -----------------------------------------

    def test_dry_run_does_not_capture_or_call_provider(self) -> None:
        provider = _FakeProvider()
        adapter = self._adapter(provider=provider)
        result = adapter.execute(self._proposal(
            "desktop.ocr_foreground", dry_run=True,
        ))
        self.assertEqual(result.status, "executed")
        self.assertTrue(result.output["dry_run"])
        self.assertEqual(result.output["text"], "")
        self.assertIsNone(result.output["screenshot"])
        self.assertEqual(list(self.tmp.glob("*.png")), [])
        self.assertIsNone(provider.last_input)

    def test_non_windows_platform_fails_honestly(self) -> None:
        provider = _FakeProvider()
        adapter = DesktopCapability(
            screenshots_dir=self.tmp,
            platform="linux",
            ocr_provider=provider,
        )
        result = adapter.execute(self._proposal("desktop.ocr_foreground"))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.output["error"], "platform_unsupported")
        self.assertIsNone(provider.last_input)

    # --- Caps -------------------------------------------------------------

    def test_text_truncation_cap_kicks_in(self) -> None:
        # Build a string strictly larger than the byte cap.
        big = "a" * (_MAX_OCR_TEXT_BYTES + 64)
        provider = _FakeProvider(text=big, lines=[OCRLine(text="x")])
        adapter = self._adapter(provider=provider)
        result = adapter.execute(self._proposal("desktop.ocr_foreground"))
        self.assertEqual(result.status, "executed")
        out = result.output
        self.assertTrue(out["truncated"])
        self.assertLessEqual(len(out["text"].encode("utf-8")), _MAX_OCR_TEXT_BYTES)
        # Pre-truncation byte count is preserved for honest reporting.
        self.assertEqual(out["byte_count"], len(big.encode("utf-8")))

    def test_line_count_cap_records_truncation(self) -> None:
        many = [OCRLine(text=f"line {i}") for i in range(_MAX_OCR_LINES + 5)]
        provider = _FakeProvider(text="ok", lines=many)
        adapter = self._adapter(provider=provider)
        result = adapter.execute(self._proposal("desktop.ocr_foreground"))
        self.assertEqual(result.status, "executed")
        self.assertTrue(result.output["truncated"])
        # Reported line_count remains the full count (honesty); the
        # serialised list is what gets clipped.
        self.assertEqual(result.output["line_count"], len(many))
        self.assertEqual(len(result.output["lines"]), _MAX_OCR_LINES)


# ---------------------------------------------------------------------------
# Planner mappings
# ---------------------------------------------------------------------------


class PlannerOCRRulesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = DeterministicPlanner()

    def _plan(self, text):
        return self.planner.plan(text)

    # foreground default
    def test_ocr_my_window(self) -> None:
        for phrase in [
            "ocr my window",
            "ocr my screen",
            "ocr my current window",
            "ocr foreground window",
            "ocr the active window",
            "ocr this window",
            "OCR My Window",
        ]:
            r = self._plan(phrase)
            self.assertEqual(r.status, MAPPED, phrase)
            self.assertEqual(r.capability, "desktop.ocr_foreground", phrase)

    def test_read_text_from_window_or_screen(self) -> None:
        for phrase in [
            "read text from my current window",
            "read text from my screen",
            "read the text from this window",
            "extract text from my window",
            "extract text from the screen",
        ]:
            r = self._plan(phrase)
            self.assertEqual(r.status, MAPPED, phrase)
            self.assertEqual(r.capability, "desktop.ocr_foreground", phrase)

    def test_what_text_questions(self) -> None:
        for phrase in [
            "what text is on my screen?",
            "what text is in this window?",
            "what does the window say?",
            "what does my screen say",
        ]:
            r = self._plan(phrase)
            self.assertEqual(r.status, MAPPED, phrase)
            self.assertEqual(r.capability, "desktop.ocr_foreground", phrase)

    def test_screenshot_and_read_composite(self) -> None:
        for phrase in [
            "take a screenshot and read it",
            "take a screenshot and ocr it",
            "capture a screenshot then read it",
            "grab a screenshot and ocr",
        ]:
            r = self._plan(phrase)
            self.assertEqual(r.status, MAPPED, phrase)
            self.assertEqual(r.capability, "desktop.ocr_foreground", phrase)

    # full screen variants
    def test_full_screen_ocr_phrases(self) -> None:
        for phrase in [
            "ocr full screen",
            "ocr the entire desktop",
            "ocr the whole screen",
            "ocr the desktop",
            "what text is on my full screen?",
            "what text is on the entire desktop?",
            "extract text from the full screen",
            "take a full screen screenshot and read it",
        ]:
            r = self._plan(phrase)
            self.assertEqual(r.status, MAPPED, phrase)
            self.assertEqual(r.capability, "desktop.ocr_full", phrase)

    # screenshot-by-name
    def test_ocr_screenshot_file(self) -> None:
        r = self._plan("ocr screenshot-abc-123.png")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "desktop.ocr_screenshot")
        self.assertEqual(r.parameters["name"], "screenshot-abc-123.png")

    def test_ocr_does_not_swallow_plain_screenshot(self) -> None:
        # The pure-screenshot rule must still fire for "take a screenshot"
        # alone (no "and read it").
        r = self._plan("take a screenshot")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "desktop.screenshot_foreground")

    def test_ocr_does_not_swallow_what_is_on_my_screen(self) -> None:
        # Without "text", "what is on my screen" stays a screenshot.
        r = self._plan("what is on my screen")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "desktop.screenshot_foreground")

    def test_unsupported_returns_unsupported(self) -> None:
        r = self._plan("photoshop my screen")
        self.assertEqual(r.status, UNSUPPORTED)


# ---------------------------------------------------------------------------
# End-to-end via LocalSupervisorAPI
# ---------------------------------------------------------------------------


class _OcrEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        workspace = Path(__file__).resolve().parents[3]
        self.root = workspace / "runtime" / f"ocr-e2e-{uuid4()}"
        (self.root / "configs").mkdir(parents=True, exist_ok=True)
        (self.root / "configs" / "policy.default.json").write_text(
            (workspace / "configs" / "policy.default.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.api = LocalSupervisorAPI(self.root)
        # Inject a fake OCR provider into the desktop adapter that the
        # API just constructed. This avoids any winsdk import at test
        # time and pins behaviour.
        for adapter in self.api.gateway.adapters:
            if isinstance(adapter, DesktopCapability):
                adapter._ocr_provider = _FakeProvider(text="hello jarvis")
                # Also stub the capture so the test runs on Linux/macOS CI.
                adapter._capture_foreground = lambda: (2, 2, _tiny_rgb(2, 2))
                adapter._capture_full = lambda: (3, 1, _tiny_rgb(3, 1))
                adapter._platform = "win32"
        # Make sure the screenshots dir exists for the bridge.
        self.api.screenshots_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_planner_routes_ocr_my_current_window_through_gateway(self) -> None:
        async def run():
            return await self.api.submit_voice_or_text_task("ocr my current window")
        task = asyncio.run(run())
        plan = task.context.get("plan") or {}
        self.assertEqual(plan.get("capability"), "desktop.ocr_foreground")
        # The auto-planner submits the proposal through the supervisor.
        plan_action = task.context.get("planAction") or {}
        self.assertEqual(plan_action.get("capability"), "desktop.ocr_foreground")
        self.assertEqual(plan_action.get("status"), "executed")
        # Result lands in supervisor.action_results.
        latest = self.api.supervisor.latest_action_result()
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.proposal.capability, "desktop.ocr_foreground")
        self.assertEqual(latest.output["text"], "hello jarvis")
        # Bridge view exposes the latestOcr entry honestly.
        view = _build_desktop_view(self.api.supervisor)
        self.assertIsNotNone(view)
        assert view is not None
        ocr = view.get("latestOcr")
        self.assertIsNotNone(ocr)
        assert ocr is not None
        self.assertEqual(ocr["text"], "hello jarvis")
        self.assertEqual(ocr["mode"], "foreground")
        self.assertEqual(ocr["provider"], "fake")
        # latestScreenshot should also point at the OCR's source PNG so
        # the HUD can preview it.
        latest_shot = view.get("latestScreenshot")
        self.assertIsNotNone(latest_shot)
        assert latest_shot is not None
        self.assertTrue(str(latest_shot.get("name") or "").startswith("screenshot-"))


# ---------------------------------------------------------------------------
# Bridge endpoint smoke (latestOcr surfaces in /hud-state)
# ---------------------------------------------------------------------------


class OcrBridgeViewTests(unittest.TestCase):
    def setUp(self) -> None:
        workspace = Path(__file__).resolve().parents[3]
        self.root = workspace / "runtime" / f"ocr-bridge-{uuid4()}"
        (self.root / "configs").mkdir(parents=True, exist_ok=True)
        (self.root / "configs" / "policy.default.json").write_text(
            (workspace / "configs" / "policy.default.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.api = LocalSupervisorAPI(self.root)
        for adapter in self.api.gateway.adapters:
            if isinstance(adapter, DesktopCapability):
                adapter._ocr_provider = _FakeProvider(text="bridge text")
                adapter._capture_foreground = lambda: (2, 2, _tiny_rgb(2, 2))
                adapter._platform = "win32"
        self.api.screenshots_root.mkdir(parents=True, exist_ok=True)
        self.server = start_server(self.api, port=0, daemon=True)
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass
        shutil.rmtree(self.root, ignore_errors=True)

    def test_hud_state_includes_latest_ocr_after_action(self) -> None:
        # Submit through HTTP end-to-end.
        import json as _json
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/tasks", body=_json.dumps({
            "objective": "ocr my current window",
        }), headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        self.assertIn(resp.status, (200, 201))

        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/hud-state")
        resp = conn.getresponse()
        body = _json.loads(resp.read().decode("utf-8"))
        self.assertEqual(resp.status, 200)
        desktop = body.get("desktop") or {}
        ocr = desktop.get("latestOcr")
        self.assertIsNotNone(ocr)
        assert ocr is not None
        self.assertEqual(ocr["text"], "bridge text")
        self.assertEqual(ocr["mode"], "foreground")
        # The screenshots PNG is served by /screenshots/<name>.
        name = ocr.get("screenshotName")
        self.assertTrue(isinstance(name, str) and name.startswith("screenshot-"))
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", f"/screenshots/{name}")
        resp = conn.getresponse()
        png = resp.read()
        self.assertEqual(resp.status, 200)
        self.assertTrue(png.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
