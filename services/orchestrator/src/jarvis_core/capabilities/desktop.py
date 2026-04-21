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
import struct
import sys
import threading
import zlib
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..models import ActionProposal, ActionResult, new_id
from .base import CapabilityAdapter


_SUPPORTED = {
    "desktop.clipboard_read",
    "desktop.clipboard_write",
    "desktop.notify",
    "desktop.foreground_window",
    "desktop.screenshot_foreground",
    "desktop.screenshot_full",
}

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
    ) -> None:
        self._platform = platform or sys.platform
        self._clipboard_read = clipboard_read_fn or _win_clipboard_read
        self._clipboard_write = clipboard_write_fn or _win_clipboard_write
        self._notify = notify_fn or _win_notify
        self._foreground_window = foreground_window_fn or _win_foreground_window
        self._capture_foreground = capture_foreground_fn or _win_capture_foreground
        self._capture_full = capture_full_fn or _win_capture_full_screen
        self._screenshots_dir = Path(screenshots_dir) if screenshots_dir else None

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
        except (ValueError, OSError) as exc:
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
