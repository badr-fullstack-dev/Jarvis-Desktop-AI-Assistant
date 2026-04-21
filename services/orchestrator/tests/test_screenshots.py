"""Tests for screen & UI awareness v1: screenshots + bridge endpoint.

Real Win32 GDI calls are never made. The adapter accepts injected
``capture_foreground_fn`` / ``capture_full_fn`` hooks that hand it a
tiny (width, height, RGB bytes) tuple — enough to exercise the PNG
encoder, file writer, output shape, and platform guard.
"""

from __future__ import annotations

import shutil
import struct
import unittest
import zlib
from http.client import HTTPConnection
from pathlib import Path
from uuid import uuid4

from src.jarvis_core.api import LocalSupervisorAPI
from src.jarvis_core.bridge import _SCREENSHOT_NAME_RE, start_server
from src.jarvis_core.capabilities.desktop import (
    DesktopCapability,
    _rgb_to_png,
)
from src.jarvis_core.models import ActionProposal


def _tiny_rgb(w: int, h: int, color=(10, 20, 30)) -> bytes:
    pixel = bytes(color)
    return pixel * (w * h)


class PngEncoderTests(unittest.TestCase):
    def test_encoder_emits_valid_png_signature_and_chunks(self) -> None:
        data = _rgb_to_png(2, 2, _tiny_rgb(2, 2, (255, 0, 0)))
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        # IHDR chunk sits right after the signature: 4B length, "IHDR".
        self.assertEqual(data[12:16], b"IHDR")
        # IEND is always last, fixed 12 bytes.
        self.assertEqual(data[-8:-4], b"IEND")

    def test_encoder_roundtrips_through_zlib_for_pixel_data(self) -> None:
        # Decode IDAT back out and verify the scanlines we fed in.
        w, h = 3, 1
        rgb = bytes([1, 2, 3, 4, 5, 6, 7, 8, 9])
        data = _rgb_to_png(w, h, rgb)
        # Walk the chunks to find IDAT.
        i = 8
        idat = b""
        while i < len(data):
            length = struct.unpack(">I", data[i:i + 4])[0]
            tag = data[i + 4:i + 8]
            body = data[i + 8:i + 8 + length]
            i += 8 + length + 4
            if tag == b"IDAT":
                idat += body
        raw = zlib.decompress(idat)
        # 1-byte filter prefix per row, then rgb.
        self.assertEqual(raw[0], 0)
        self.assertEqual(raw[1:], rgb)

    def test_encoder_rejects_mismatched_buffer(self) -> None:
        with self.assertRaises(ValueError):
            _rgb_to_png(4, 4, b"\x00" * 5)


class DesktopScreenshotAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(__file__).resolve().parents[3] / "runtime" / f"shot-{uuid4()}"
        self.tmp.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _adapter(self, *, foreground=True, full=True) -> DesktopCapability:
        return DesktopCapability(
            capture_foreground_fn=(lambda: (2, 2, _tiny_rgb(2, 2, (200, 100, 50))))
            if foreground else None,
            capture_full_fn=(lambda: (3, 1, _tiny_rgb(3, 1, (0, 255, 0))))
            if full else None,
            screenshots_dir=self.tmp,
            platform="win32",
        )

    def test_screenshot_foreground_writes_valid_png_on_disk(self) -> None:
        adapter = self._adapter()
        result = adapter.execute(ActionProposal(
            task_id="t", capability="desktop.screenshot_foreground",
            intent="i", parameters={}, requested_by="test", evidence=[],
        ))
        self.assertEqual(result.status, "executed")
        out = result.output
        self.assertEqual(out["mode"], "foreground")
        self.assertEqual(out["width"], 2)
        self.assertEqual(out["height"], 2)
        path = Path(out["path"])
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent, self.tmp)
        self.assertTrue(path.read_bytes().startswith(b"\x89PNG"))
        self.assertEqual(out["byte_count"], path.stat().st_size)
        self.assertTrue(out["name"].startswith("screenshot-"))
        self.assertTrue(out["name"].endswith(".png"))

    def test_screenshot_full_writes_virtual_screen_shape(self) -> None:
        adapter = self._adapter()
        result = adapter.execute(ActionProposal(
            task_id="t", capability="desktop.screenshot_full",
            intent="i", parameters={}, requested_by="test", evidence=[],
        ))
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.output["mode"], "full")
        self.assertEqual(result.output["width"], 3)
        self.assertEqual(result.output["height"], 1)
        self.assertTrue(Path(result.output["path"]).is_file())

    def test_dry_run_does_not_touch_disk(self) -> None:
        adapter = self._adapter()
        result = adapter.execute(ActionProposal(
            task_id="t", capability="desktop.screenshot_foreground",
            intent="i", parameters={}, requested_by="test", evidence=[],
            dry_run=True,
        ))
        self.assertEqual(result.status, "executed")
        self.assertTrue(result.output["dry_run"])
        self.assertIsNone(result.output["path"])
        # No PNG should have been written.
        self.assertEqual(list(self.tmp.glob("*.png")), [])

    def test_non_windows_platform_fails_honestly(self) -> None:
        adapter = DesktopCapability(
            capture_foreground_fn=lambda: (2, 2, _tiny_rgb(2, 2)),
            screenshots_dir=self.tmp,
            platform="linux",
        )
        result = adapter.execute(ActionProposal(
            task_id="t", capability="desktop.screenshot_foreground",
            intent="i", parameters={}, requested_by="test", evidence=[],
        ))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.output["error"], "platform_unsupported")

    def test_capture_failure_bubbles_as_failed_action(self) -> None:
        def boom() -> tuple:
            raise OSError("GetDC failed")
        adapter = DesktopCapability(
            capture_foreground_fn=boom,
            screenshots_dir=self.tmp,
            platform="win32",
        )
        result = adapter.execute(ActionProposal(
            task_id="t", capability="desktop.screenshot_foreground",
            intent="i", parameters={}, requested_by="test", evidence=[],
        ))
        self.assertEqual(result.status, "failed")
        self.assertIn("GetDC", result.output["error"])


class ScreenshotNameRegexTests(unittest.TestCase):
    def test_accepts_canonical_names(self) -> None:
        self.assertIsNotNone(_SCREENSHOT_NAME_RE.match(
            "screenshot-abc123_def-ghi.png"))

    def test_rejects_path_traversal_and_junk(self) -> None:
        for bad in [
            "../secrets.png",
            "screenshot-../x.png",
            "screenshot-abc/evil.png",
            "notes.txt",
            "screenshot-.png",
            "SCREENSHOT-x.png",  # planner-written names are lowercase prefix
            "screenshot-x.PNG",
            "",
        ]:
            self.assertIsNone(_SCREENSHOT_NAME_RE.match(bad), bad)


class _BridgeTestBase(unittest.TestCase):
    """Spin up a real bridge server on an ephemeral port."""

    def setUp(self) -> None:
        self.workspace_root = Path(__file__).resolve().parents[3]
        self.root = self.workspace_root / "runtime" / f"shot-bridge-{uuid4()}"
        (self.root / "configs").mkdir(parents=True, exist_ok=True)
        (self.root / "configs" / "policy.default.json").write_text(
            (self.workspace_root / "configs" / "policy.default.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.api = LocalSupervisorAPI(self.root)
        # Port 0 means "pick one" — read the bound port back off the server.
        self.server = start_server(self.api, port=0, daemon=True)
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass
        shutil.rmtree(self.root, ignore_errors=True)


class ScreenshotBridgeEndpointTests(_BridgeTestBase):
    def _write_fixture(self, name: str, body: bytes) -> Path:
        self.api.screenshots_root.mkdir(parents=True, exist_ok=True)
        p = self.api.screenshots_root / name
        p.write_bytes(body)
        return p

    def test_serves_a_real_png(self) -> None:
        name = f"screenshot-{uuid4()}.png"
        png = _rgb_to_png(2, 2, _tiny_rgb(2, 2, (5, 10, 15)))
        self._write_fixture(name, png)
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", f"/screenshots/{name}")
        resp = conn.getresponse()
        body = resp.read()
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Content-Type"), "image/png")
        self.assertEqual(body, png)

    def test_rejects_path_traversal(self) -> None:
        # Plant a file outside the screenshots root that shouldn't be served.
        sneaky = self.root / "runtime" / "secret.png"
        sneaky.parent.mkdir(parents=True, exist_ok=True)
        sneaky.write_bytes(b"\x89PNGsecret")

        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        # The raw-URL traversal attempt.
        conn.request("GET", "/screenshots/../secret.png")
        resp = conn.getresponse()
        resp.read()
        self.assertEqual(resp.status, 404)

    def test_rejects_unknown_name_shape(self) -> None:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/screenshots/random.png")
        resp = conn.getresponse()
        resp.read()
        self.assertEqual(resp.status, 404)


if __name__ == "__main__":
    unittest.main()
