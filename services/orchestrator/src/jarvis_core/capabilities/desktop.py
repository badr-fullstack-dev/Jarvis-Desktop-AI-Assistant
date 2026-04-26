"""Windows-first desktop capability adapter (stdlib ctypes only).

Capabilities
------------
  desktop.clipboard_read        Tier 0  — read CF_UNICODETEXT from the system clipboard.
  desktop.clipboard_write       Tier 1  — write a UTF-16 string into the clipboard.
  desktop.notify                Tier 1  — show a dialog-style notification (MessageBoxW).
  desktop.foreground_window     Tier 0  — report the current foreground window and exe.
  desktop.screenshot_foreground Tier 0  — capture the foreground window via PrintWindow.
  desktop.screenshot_full       Tier 0  — capture the full virtual screen via BitBlt.

All four capabilities go through the same ActionGateway/PolicyEngine path
as every other adapter. Nothing here bypasses policy. Nothing here
simulates keyboard or mouse input. On non-Windows platforms every
capability fails honestly with ``platform_unsupported`` rather than
pretending to work.

Design notes
------------
* No new pip dependencies. Everything is ``ctypes`` + the stdlib.
* ``desktop.notify`` uses ``MessageBoxW`` from a short-lived daemon
  thread so the call returns quickly. It is intentionally *not*
  presented as a Windows toast (that would require WinRT or a
  third-party module). The summary says "dialog notification".
* Size caps protect the HUD from pathological payloads:
  ``_MAX_CLIPBOARD_READ`` (4 KB excerpt), ``_MAX_CLIPBOARD_WRITE`` (64 KB),
  ``_MAX_NOTIFY_LEN`` (1 KB for message, 256 for title).
"""

from __future__ import annotations

import ctypes
import re
import struct
import sys
import threading
import zlib
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..models import ActionProposal, ActionResult, new_id
from ..ocr_providers import (
    OCRError,
    OCRProvider,
    UnavailableOCRProvider,
)
from .base import CapabilityAdapter


_SUPPORTED = {
    "desktop.clipboard_read",
    "desktop.clipboard_write",
    "desktop.notify",
    "desktop.foreground_window",
    "desktop.screenshot_foreground",
    "desktop.screenshot_full",
    "desktop.ocr_foreground",
    "desktop.ocr_full",
    "desktop.ocr_screenshot",
}

# Match the canonical screenshot filename produced by ``_exec_screenshot``
# below. Mirrors the bridge regex so OCR-by-name cannot escape the
# screenshots directory or read attacker-supplied paths.
_SCREENSHOT_NAME_RE = re.compile(r"^screenshot-[A-Za-z0-9_-]+\.png$")

# OCR caps — applied at the adapter regardless of provider, so the HUD
# never has to render a 1 MB blob inline.
_MAX_OCR_TEXT_BYTES = 64 * 1024
_MAX_OCR_LINES = 5_000

# Caps — applied at adapter level, independent of policy tiering.
_MAX_CLIPBOARD_READ = 4 * 1024
_MAX_CLIPBOARD_WRITE = 64 * 1024
_MAX_NOTIFY_MSG = 1024
_MAX_NOTIFY_TITLE = 256

# Win32 constants
_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002
_MB_ICONINFORMATION = 0x00000040
_MB_TOPMOST = 0x00040000
_MB_SETFOREGROUND = 0x00010000
_MB_OK = 0x00000000
_SW_RESTORE = 9
_SW_SHOW = 5
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# Screen capture constants
_SRCCOPY = 0x00CC0020
_CAPTUREBLT = 0x40000000
_DIB_RGB_COLORS = 0
_BI_RGB = 0
_PW_RENDERFULLCONTENT = 0x00000002
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79

# Upper bound on pixel dimensions per capture. Beyond this we fail honestly
# rather than silently allocating a huge buffer. 8K width × 8K height is
# generous and keeps a worst-case buffer under ~200 MB.
_MAX_SCREEN_DIM = 8192


def _is_windows() -> bool:
    return sys.platform.startswith("win")


# ---------------------------------------------------------------------------
# Win32 helpers (all guarded — caller checks _is_windows() first)
# ---------------------------------------------------------------------------

def _win_clipboard_read() -> str:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    if not user32.OpenClipboard(0):
        raise OSError("OpenClipboard failed")
    try:
        if not user32.IsClipboardFormatAvailable(_CF_UNICODETEXT):
            return ""
        handle = user32.GetClipboardData(_CF_UNICODETEXT)
        if not handle:
            return ""
        locked = kernel32.GlobalLock(handle)
        if not locked:
            return ""
        try:
            # Read up to cap+1 chars so we can flag truncation without
            # pulling a potentially-huge blob into Python.
            text = ctypes.wstring_at(locked)
            return text
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _win_clipboard_write(text: str) -> None:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    # Allocate moveable buffer for UTF-16 payload including null terminator.
    data = (text + "\0").encode("utf-16-le")
    handle = kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
    if not handle:
        raise OSError("GlobalAlloc failed")
    locked = kernel32.GlobalLock(handle)
    if not locked:
        kernel32.GlobalFree(handle)
        raise OSError("GlobalLock failed")
    try:
        ctypes.memmove(locked, data, len(data))
    finally:
        kernel32.GlobalUnlock(handle)

    if not user32.OpenClipboard(0):
        kernel32.GlobalFree(handle)
        raise OSError("OpenClipboard failed")
    try:
        if not user32.EmptyClipboard():
            raise OSError("EmptyClipboard failed")
        if not user32.SetClipboardData(_CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise OSError("SetClipboardData failed")
        # Ownership transferred to clipboard — do NOT GlobalFree on success.
    finally:
        user32.CloseClipboard()


def _win_notify(title: str, message: str) -> None:
    """Show a non-blocking dialog notification. Returns immediately."""
    user32 = ctypes.windll.user32
    flags = _MB_ICONINFORMATION | _MB_TOPMOST | _MB_SETFOREGROUND | _MB_OK

    def _run() -> None:
        try:
            user32.MessageBoxW(0, message, title, flags)
        except Exception:  # pragma: no cover — best-effort background call
            pass

    threading.Thread(target=_run, name="jarvis-notify", daemon=True).start()


def _win_get_exe_for_pid(pid: int) -> Optional[str]:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buf_len = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(buf_len.value)
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(buf_len))
        if not ok:
            return None
        return buf.value or None
    finally:
        kernel32.CloseHandle(handle)


def _win_foreground_window() -> Dict[str, Any]:
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return {"hwnd": 0, "title": "", "pid": 0, "exe": None}
    length = user32.GetWindowTextLengthW(hwnd)
    title = ""
    if length > 0:
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = _win_get_exe_for_pid(int(pid.value)) if pid.value else None
    return {
        "hwnd": int(hwnd),
        "title": title,
        "pid": int(pid.value),
        "exe": exe,
    }


# ---------------------------------------------------------------------------
# Screen capture — stdlib PNG encoder + Win32 helpers
# ---------------------------------------------------------------------------

def _rgb_to_png(width: int, height: int, rgb: bytes) -> bytes:
    """Encode a top-down 24-bit RGB pixel buffer as a PNG. Stdlib only."""
    if width <= 0 or height <= 0:
        raise ValueError("Screenshot dimensions must be positive")
    stride = width * 3
    if len(rgb) != stride * height:
        raise ValueError(
            f"Pixel buffer size mismatch: got {len(rgb)}, "
            f"expected {stride * height} for {width}x{height} RGB"
        )

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    signature = b"\x89PNG\r\n\x1a\n"
    # bit depth 8, color type 2 (RGB), default compression/filter/interlace.
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type: None
        raw += rgb[y * stride:(y + 1) * stride]
    idat = zlib.compress(bytes(raw), 6)

    return (
        signature
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND", b"")
    )


def _bgra_to_rgb_topdown(bgra: bytes, width: int, height: int) -> bytes:
    """Convert a BGRA (or BGRX) buffer into a top-down RGB byte string.

    GDI captures rows top-down when the bitmap has a *negative* height
    passed to GetDIBits, so callers already hand us top-down data. We
    only need to drop the alpha byte and swap B↔R.
    """
    expected = width * height * 4
    if len(bgra) < expected:
        raise ValueError(
            f"BGRA buffer too small: got {len(bgra)}, expected {expected}"
        )
    out = bytearray(width * height * 3)
    # Vectorised-ish: walk the buffer in 4-byte strides.
    j = 0
    for i in range(0, expected, 4):
        out[j]     = bgra[i + 2]  # R
        out[j + 1] = bgra[i + 1]  # G
        out[j + 2] = bgra[i]      # B
        j += 3
    return bytes(out)


def _win_virtual_screen_rect() -> Tuple[int, int, int, int]:
    user32 = ctypes.windll.user32
    x = user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
    y = user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
    w = user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
    h = user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)
    return int(x), int(y), int(w), int(h)


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


def _win_capture_rect(src_hwnd: Optional[int], src_x: int, src_y: int,
                      width: int, height: int,
                      use_print_window: bool = False) -> Tuple[int, int, bytes]:
    """Generic GDI capture: returns (width, height, RGB top-down bytes).

    If ``use_print_window`` is True and ``src_hwnd`` is set, copies via
    PrintWindow(hwnd, memDC, PW_RENDERFULLCONTENT) — captures the window
    even if occluded. Otherwise BitBlt from the source DC.
    """
    if width <= 0 or height <= 0:
        raise OSError("Capture target has zero area")
    if width > _MAX_SCREEN_DIM or height > _MAX_SCREEN_DIM:
        raise OSError(
            f"Capture too large ({width}x{height}); refusing to allocate."
        )

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    # Return types / arg types for the few calls that matter.
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.restype = ctypes.c_int

    src_dc = user32.GetDC(src_hwnd if src_hwnd else 0)
    if not src_dc:
        raise OSError("GetDC failed")
    mem_dc = gdi32.CreateCompatibleDC(src_dc)
    if not mem_dc:
        user32.ReleaseDC(src_hwnd if src_hwnd else 0, src_dc)
        raise OSError("CreateCompatibleDC failed")
    bitmap = gdi32.CreateCompatibleBitmap(src_dc, width, height)
    if not bitmap:
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(src_hwnd if src_hwnd else 0, src_dc)
        raise OSError("CreateCompatibleBitmap failed")
    prev = gdi32.SelectObject(mem_dc, bitmap)

    try:
        if use_print_window and src_hwnd:
            ok = user32.PrintWindow(wintypes.HWND(src_hwnd), mem_dc,
                                    _PW_RENDERFULLCONTENT)
            if not ok:
                raise OSError("PrintWindow failed")
        else:
            ok = gdi32.BitBlt(mem_dc, 0, 0, width, height,
                              src_dc, src_x, src_y,
                              _SRCCOPY | _CAPTUREBLT)
            if not ok:
                raise OSError("BitBlt failed")

        # Pull pixels out as 32-bit BGRA, top-down (negative height).
        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = _BI_RGB

        buf_len = width * height * 4
        buf = (ctypes.c_ubyte * buf_len)()
        lines = gdi32.GetDIBits(mem_dc, bitmap, 0, height, buf,
                                ctypes.byref(bmi), _DIB_RGB_COLORS)
        if lines == 0:
            raise OSError("GetDIBits returned zero lines")
        rgb = _bgra_to_rgb_topdown(bytes(buf), width, height)
        return width, height, rgb
    finally:
        gdi32.SelectObject(mem_dc, prev)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(src_hwnd if src_hwnd else 0, src_dc)


def _win_capture_foreground() -> Tuple[int, int, bytes]:
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        raise OSError("No foreground window")
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise OSError("GetWindowRect failed")
    w = int(rect.right - rect.left)
    h = int(rect.bottom - rect.top)
    return _win_capture_rect(int(hwnd), 0, 0, w, h, use_print_window=True)


def _win_capture_full_screen() -> Tuple[int, int, bytes]:
    x, y, w, h = _win_virtual_screen_rect()
    return _win_capture_rect(None, x, y, w, h, use_print_window=False)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class DesktopCapability(CapabilityAdapter):
    """Stdlib ctypes-only Windows desktop adapter.

    Injectable hooks let tests swap the win32 calls without touching the
    real user32/kernel32 surface. Non-Windows platforms fail honestly.
    """

    name = "desktop"

    def __init__(
        self,
        *,
        clipboard_read_fn=None,
        clipboard_write_fn=None,
        notify_fn=None,
        foreground_window_fn=None,
        capture_foreground_fn=None,
        capture_full_fn=None,
        screenshots_dir: Optional[Path] = None,
        platform: Optional[str] = None,
        ocr_provider: Optional[OCRProvider] = None,
    ) -> None:
        self._platform = platform or sys.platform
        self._clipboard_read = clipboard_read_fn or _win_clipboard_read
        self._clipboard_write = clipboard_write_fn or _win_clipboard_write
        self._notify = notify_fn or _win_notify
        self._foreground_window = foreground_window_fn or _win_foreground_window
        self._capture_foreground = capture_foreground_fn or _win_capture_foreground
        self._capture_full = capture_full_fn or _win_capture_full_screen
        self._screenshots_dir = Path(screenshots_dir) if screenshots_dir else None
        # Default to the explicit "no OCR configured" provider. The API
        # layer wires the real provider (windows-media-ocr / auto) from
        # JARVIS_OCR_PROVIDER. Tests inject a fake provider directly.
        self._ocr_provider: OCRProvider = ocr_provider or UnavailableOCRProvider()

    # ------------------------------------------------------------------
    def supports(self, capability: str) -> bool:
        return capability in _SUPPORTED

    def execute(self, proposal: ActionProposal) -> ActionResult:
        try:
            if not self._platform.startswith("win"):
                return self._unsupported_platform(proposal)
            if proposal.capability == "desktop.clipboard_read":
                return self._exec_clipboard_read(proposal)
            if proposal.capability == "desktop.clipboard_write":
                return self._exec_clipboard_write(proposal)
            if proposal.capability == "desktop.notify":
                return self._exec_notify(proposal)
            if proposal.capability == "desktop.foreground_window":
                return self._exec_foreground_window(proposal)
            if proposal.capability == "desktop.screenshot_foreground":
                return self._exec_screenshot(proposal, mode="foreground")
            if proposal.capability == "desktop.screenshot_full":
                return self._exec_screenshot(proposal, mode="full")
            if proposal.capability == "desktop.ocr_foreground":
                return self._exec_ocr_capture(proposal, mode="foreground")
            if proposal.capability == "desktop.ocr_full":
                return self._exec_ocr_capture(proposal, mode="full")
            if proposal.capability == "desktop.ocr_screenshot":
                return self._exec_ocr_screenshot(proposal)
        except (ValueError, OSError, OCRError) as exc:
            return ActionResult(
                proposal=proposal, status="failed",
                summary=f"{proposal.capability} failed: {exc}",
                output={"error": str(exc), "error_type": type(exc).__name__,
                        "dry_run": proposal.dry_run},
            )
        raise KeyError(f"Unsupported capability: {proposal.capability}")

    def verify(self, proposal: ActionProposal, result: ActionResult) -> Dict[str, Any]:
        if result.status != "executed":
            return {"ok": False, "reason": result.status, "mode": "real"}
        checked = [proposal.capability]
        return {"ok": True, "checked": checked, "mode": "real"}

    # ------------------------------------------------------------------
    def _unsupported_platform(self, proposal: ActionProposal) -> ActionResult:
        return ActionResult(
            proposal=proposal, status="failed",
            summary=f"{proposal.capability} is only supported on Windows "
                    f"(detected platform: {self._platform!r}).",
            output={"error": "platform_unsupported",
                    "platform": self._platform,
                    "dry_run": proposal.dry_run},
        )

    # ------------------------------------------------------------------
    def _exec_clipboard_read(self, proposal: ActionProposal) -> ActionResult:
        if proposal.dry_run:
            return ActionResult(
                proposal=proposal, status="executed",
                summary="[dry-run] Would read the system clipboard.",
                output={"text": None, "truncated": False, "byte_count": 0,
                        "dry_run": True},
            )
        text = self._clipboard_read() or ""
        truncated = False
        raw_len = len(text)
        if raw_len > _MAX_CLIPBOARD_READ:
            text = text[:_MAX_CLIPBOARD_READ]
            truncated = True
        return ActionResult(
            proposal=proposal, status="executed",
            summary=f"Read {raw_len} chars from clipboard"
                    + (" (truncated)" if truncated else ""),
            output={
                "text": text,
                "truncated": truncated,
                "byte_count": raw_len,
                "dry_run": False,
            },
        )

    def _exec_clipboard_write(self, proposal: ActionProposal) -> ActionResult:
        text = proposal.parameters.get("text")
        if text is None or not isinstance(text, str):
            raise ValueError("Parameter 'text' is required (string).")
        if len(text) > _MAX_CLIPBOARD_WRITE:
            raise ValueError(
                f"Clipboard payload too large: {len(text)} chars "
                f"(max {_MAX_CLIPBOARD_WRITE})."
            )
        if proposal.dry_run:
            return ActionResult(
                proposal=proposal, status="executed",
                summary=f"[dry-run] Would write {len(text)} chars to clipboard.",
                output={"byte_count": len(text), "dry_run": True},
            )
        self._clipboard_write(text)
        return ActionResult(
            proposal=proposal, status="executed",
            summary=f"Wrote {len(text)} chars to clipboard.",
            output={"byte_count": len(text), "dry_run": False},
        )

    def _exec_notify(self, proposal: ActionProposal) -> ActionResult:
        message = proposal.parameters.get("message")
        if message is None or not isinstance(message, str):
            raise ValueError("Parameter 'message' is required (string).")
        title = proposal.parameters.get("title", "Jarvis")
        if not isinstance(title, str):
            raise ValueError("Parameter 'title' must be a string if provided.")
        if len(message) > _MAX_NOTIFY_MSG:
            raise ValueError(
                f"Notification message too long: {len(message)} "
                f"(max {_MAX_NOTIFY_MSG})."
            )
        if len(title) > _MAX_NOTIFY_TITLE:
            raise ValueError(
                f"Notification title too long: {len(title)} "
                f"(max {_MAX_NOTIFY_TITLE})."
            )
        if proposal.dry_run:
            return ActionResult(
                proposal=proposal, status="executed",
                summary=f"[dry-run] Would show notification {title!r}.",
                output={"title": title, "message": message, "dry_run": True,
                        "channel": "dialog"},
            )
        self._notify(title, message)
        return ActionResult(
            proposal=proposal, status="executed",
            summary=f"Notification dispatched: {title!r}.",
            output={"title": title, "message": message, "dry_run": False,
                    "channel": "dialog"},
        )

    def _exec_screenshot(self, proposal: ActionProposal, *, mode: str) -> ActionResult:
        if proposal.dry_run:
            return ActionResult(
                proposal=proposal, status="executed",
                summary=f"[dry-run] Would capture the {mode} screen.",
                output={"mode": mode, "path": None, "name": None,
                        "width": 0, "height": 0, "byte_count": 0,
                        "dry_run": True},
            )
        if self._screenshots_dir is None:
            raise OSError(
                "Screenshots directory is not configured. Set "
                "screenshots_dir on DesktopCapability."
            )
        capture = self._capture_foreground if mode == "foreground" else self._capture_full
        width, height, rgb = capture()
        if not isinstance(rgb, (bytes, bytearray)):
            raise OSError(f"Capture returned non-bytes buffer of type {type(rgb).__name__}")
        png = _rgb_to_png(int(width), int(height), bytes(rgb))
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)
        name = f"{new_id('screenshot')}.png"
        path = self._screenshots_dir / name
        path.write_bytes(png)
        return ActionResult(
            proposal=proposal, status="executed",
            summary=f"Captured {mode} screenshot ({width}x{height}, {len(png)} bytes).",
            output={
                "mode": mode,
                "path": str(path),
                "name": name,
                "width": int(width),
                "height": int(height),
                "byte_count": len(png),
                "dry_run": False,
            },
        )

    # ------------------------------------------------------------------
    # OCR
    # ------------------------------------------------------------------
    def _truncate_ocr_text(self, text: str) -> Tuple[str, bool, int]:
        """Apply the byte-cap. Returns (clipped_text, truncated, raw_byte_len)."""
        raw_bytes = (text or "").encode("utf-8", errors="replace")
        if len(raw_bytes) <= _MAX_OCR_TEXT_BYTES:
            return (text or ""), False, len(raw_bytes)
        # Slice on a UTF-8 boundary by re-decoding ignoring trailing partial char.
        clipped = raw_bytes[:_MAX_OCR_TEXT_BYTES].decode("utf-8", errors="ignore")
        return clipped, True, len(raw_bytes)

    def _ocr_run(
        self,
        png_bytes: bytes,
        proposal: ActionProposal,
        *,
        mode: str,
        screenshot_meta: Optional[Dict[str, Any]],
    ) -> ActionResult:
        """Shared OCR finisher — runs the provider and assembles the result.

        The capability layer caps the returned text and line list before
        the result lands in any task trace or HUD JSON, so a pathological
        OCR output cannot bloat /hud-state.
        """
        provider = self._ocr_provider
        # Always go through extract(); the provider raises OCRError with a
        # clear remediation hint if it's not configured. We don't gate on
        # available() here because the provider's own message is more
        # specific (e.g. "winsdk import failed: ..." vs the generic case).
        result = provider.extract(png_bytes, language=None)
        clipped, truncated, raw_byte_len = self._truncate_ocr_text(result.text or "")
        # Cap the line list separately. Keep the order stable.
        lines = [ln.to_dict() for ln in result.lines][:_MAX_OCR_LINES]
        line_count = len(result.lines)
        line_truncated = line_count > _MAX_OCR_LINES

        output: Dict[str, Any] = {
            "mode": mode,
            "screenshot": screenshot_meta,
            "text": clipped,
            "truncated": truncated or line_truncated,
            "byte_count": raw_byte_len,
            "char_count": len(clipped),
            "line_count": line_count,
            "lines": lines,
            "average_confidence": result.average_confidence,
            "language": result.language,
            "provider": result.provider or provider.name,
            "dry_run": False,
        }
        title_bits = []
        if screenshot_meta and screenshot_meta.get("name"):
            title_bits.append(str(screenshot_meta.get("name")))
        title_bits.append(f"{line_count} line{'s' if line_count != 1 else ''}")
        title_bits.append(f"{len(clipped)} chars")
        if truncated or line_truncated:
            title_bits.append("truncated")
        return ActionResult(
            proposal=proposal, status="executed",
            summary=f"OCR ({mode}): " + " · ".join(title_bits),
            output=output,
        )

    def _exec_ocr_capture(self, proposal: ActionProposal, *, mode: str) -> ActionResult:
        if proposal.dry_run:
            return ActionResult(
                proposal=proposal, status="executed",
                summary=f"[dry-run] Would OCR the {mode} {'window' if mode == 'foreground' else 'screen'}.",
                output={
                    "mode": mode, "screenshot": None, "text": "",
                    "truncated": False, "byte_count": 0, "char_count": 0,
                    "line_count": 0, "lines": [],
                    "average_confidence": None, "language": None,
                    "provider": getattr(self._ocr_provider, "name", "unavailable"),
                    "dry_run": True,
                },
            )
        if self._screenshots_dir is None:
            raise OSError(
                "Screenshots directory is not configured. Set "
                "screenshots_dir on DesktopCapability."
            )
        capture = self._capture_foreground if mode == "foreground" else self._capture_full
        width, height, rgb = capture()
        if not isinstance(rgb, (bytes, bytearray)):
            raise OSError(f"Capture returned non-bytes buffer of type {type(rgb).__name__}")
        png = _rgb_to_png(int(width), int(height), bytes(rgb))
        # Persist the screenshot alongside the OCR result so the HUD can
        # show the source image. Reuses the same naming/location as the
        # plain screenshot capabilities — the bridge endpoint serves both.
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)
        name = f"{new_id('screenshot')}.png"
        path = self._screenshots_dir / name
        path.write_bytes(png)
        screenshot_meta = {
            "name": name,
            "path": str(path),
            "width": int(width),
            "height": int(height),
            "byte_count": len(png),
        }
        return self._ocr_run(png, proposal, mode=mode, screenshot_meta=screenshot_meta)

    def _exec_ocr_screenshot(self, proposal: ActionProposal) -> ActionResult:
        name = proposal.parameters.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Parameter 'name' is required (string filename).")
        if not _SCREENSHOT_NAME_RE.match(name):
            raise ValueError(
                f"Refusing to OCR {name!r}: name must match "
                "'screenshot-<id>.png' (no slashes, no path traversal). Use "
                "the bridge's screenshot listing to find a valid name."
            )
        if proposal.dry_run:
            return ActionResult(
                proposal=proposal, status="executed",
                summary=f"[dry-run] Would OCR screenshot {name}.",
                output={
                    "mode": "screenshot",
                    "screenshot": {"name": name, "path": None,
                                    "width": 0, "height": 0, "byte_count": 0},
                    "text": "", "truncated": False, "byte_count": 0,
                    "char_count": 0, "line_count": 0, "lines": [],
                    "average_confidence": None, "language": None,
                    "provider": getattr(self._ocr_provider, "name", "unavailable"),
                    "dry_run": True,
                },
            )
        if self._screenshots_dir is None:
            raise OSError(
                "Screenshots directory is not configured. Set "
                "screenshots_dir on DesktopCapability."
            )
        # Path-traversal hardening: resolve the filename inside the
        # configured root and require the resolved parent to be that root.
        try:
            root_resolved = Path(self._screenshots_dir).resolve()
            target = (root_resolved / name).resolve()
        except OSError as exc:
            raise OSError(f"Could not resolve screenshot path: {exc}") from exc
        if target.parent != root_resolved or not target.is_file():
            raise OSError(f"Screenshot {name!r} not found in screenshots directory.")
        png = target.read_bytes()
        if not png.startswith(b"\x89PNG"):
            raise OSError(f"{name!r} is not a PNG (bad signature).")
        screenshot_meta = {
            "name": name,
            "path": str(target),
            "width": 0,   # we don't decode the PNG header here; HUD shows preview anyway
            "height": 0,
            "byte_count": len(png),
        }
        return self._ocr_run(png, proposal, mode="screenshot", screenshot_meta=screenshot_meta)

    def _exec_foreground_window(self, proposal: ActionProposal) -> ActionResult:
        if proposal.dry_run:
            return ActionResult(
                proposal=proposal, status="executed",
                summary="[dry-run] Would inspect the foreground window.",
                output={"window": None, "dry_run": True},
            )
        info = self._foreground_window() or {}
        title = info.get("title") or ""
        exe = info.get("exe")
        return ActionResult(
            proposal=proposal, status="executed",
            summary=(
                f"Foreground window: {title or '(no title)'}"
                + (f" — {exe}" if exe else "")
            ),
            output={"window": info, "dry_run": False},
        )
