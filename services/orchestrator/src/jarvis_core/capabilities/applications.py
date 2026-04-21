"""Real application capability adapter (Windows-first, stdlib only).

Capabilities:
  app.launch   - Tier 1. Launch an allowlisted Windows app by short name.
  app.focus    - Tier 1. Raise an already-running allowlisted app to the
                 foreground. Does NOT launch a new process. Fails honestly
                 when the target is not running, when the platform is not
                 Windows, or when Windows refuses the SetForegroundWindow
                 call (it is guarded by foreground-lock rules).
  app.install  - Tier 2. Not implemented — always returns failed with a
                 clear reason, but policy still routes through approval first.

Allowlist-only: `app.launch` never accepts arbitrary executable paths. The
allowlist maps friendly names to resolved binaries (e.g. "notepad" ->
C:\\Windows\\System32\\notepad.exe). `app.focus` reuses the same allowlist —
we only focus windows whose owning process image matches an allowlisted
executable, so we never raise an arbitrary third-party window.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..models import ActionProposal, ActionResult
from .base import CapabilityAdapter

_SUPPORTED = {"app.launch", "app.focus", "app.install"}

# Default allowlist — resolved lazily via shutil.which + known system paths.
_DEFAULT_ALLOWLIST: Dict[str, List[str]] = {
    "notepad":   ["notepad.exe"],
    "calc":      ["calc.exe"],
    "calculator": ["calc.exe"],
    "explorer":  ["explorer.exe"],
    "mspaint":   ["mspaint.exe"],
    "cmd":       [],  # explicitly empty — keep cmd out unless user configures it
}

_SW_RESTORE = 9
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _resolve_allowlist(custom: Optional[Dict[str, str]] = None) -> Dict[str, Optional[str]]:
    """Build a {name: absolute-path-or-None} map.

    Custom entries (from config) win over defaults and can be removed by
    mapping to an empty string.
    """
    resolved: Dict[str, Optional[str]] = {}
    for name, candidates in _DEFAULT_ALLOWLIST.items():
        path: Optional[str] = None
        for cand in candidates:
            found = shutil.which(cand)
            if found:
                path = found
                break
        resolved[name] = path
    if custom:
        for name, path in custom.items():
            if path:
                resolved[name] = os.path.abspath(path)
            elif name in resolved:
                resolved.pop(name, None)
    return resolved


# ---------------------------------------------------------------------------
# Win32 focus helpers
# ---------------------------------------------------------------------------

def _win_find_hwnds_for_exe(target_exe: str) -> List[Tuple[int, int, str]]:
    """Return [(hwnd, pid, exe_path), ...] for every visible top-level window
    whose owning process image matches ``target_exe`` (case-insensitive)."""
    if not sys.platform.startswith("win"):
        return []
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    target_norm = os.path.normcase(os.path.abspath(target_exe))
    matches: List[Tuple[int, int, str]] = []

    def _get_exe_for_pid(pid: int) -> Optional[str]:
        h = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return None
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
            if not ok:
                return None
            return buf.value or None
        finally:
            kernel32.CloseHandle(h)

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        # Skip tool windows / zero-length titles to keep noise down.
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return True
        exe = _get_exe_for_pid(int(pid.value))
        if not exe:
            return True
        if os.path.normcase(os.path.abspath(exe)) == target_norm:
            matches.append((int(hwnd), int(pid.value), exe))
        return True

    user32.EnumWindows(EnumWindowsProc(_cb), 0)
    return matches


def _win_focus_hwnd(hwnd: int) -> bool:
    """Try to bring ``hwnd`` to the foreground. Returns True on success.

    Windows restricts SetForegroundWindow to the foreground process and a
    few other cases. We do not attempt keyboard/mouse input tricks to
    bypass that — we call ShowWindow(SW_RESTORE) + SetForegroundWindow
    and report the real outcome.
    """
    if not sys.platform.startswith("win"):
        return False
    user32 = ctypes.windll.user32
    # Un-minimise if needed, then request foreground.
    user32.ShowWindow(hwnd, _SW_RESTORE)
    return bool(user32.SetForegroundWindow(hwnd))


class ApplicationCapability(CapabilityAdapter):
    name = "applications"

    def __init__(
        self,
        allowlist: Optional[Dict[str, str]] = None,
        *,
        find_hwnds_fn: Optional[Callable[[str], List[Tuple[int, int, str]]]] = None,
        focus_hwnd_fn: Optional[Callable[[int], bool]] = None,
        platform: Optional[str] = None,
    ) -> None:
        self._allowlist = _resolve_allowlist(allowlist)
        self._platform = platform or sys.platform
        self._find_hwnds = find_hwnds_fn or _win_find_hwnds_for_exe
        self._focus_hwnd = focus_hwnd_fn or _win_focus_hwnd

    # ------------------------------------------------------------------
    def supports(self, capability: str) -> bool:
        return capability in _SUPPORTED

    def execute(self, proposal: ActionProposal) -> ActionResult:
        try:
            if proposal.capability == "app.launch":
                return self._execute_launch(proposal)
            if proposal.capability == "app.focus":
                return self._execute_focus(proposal)
            if proposal.capability == "app.install":
                return ActionResult(
                    proposal=proposal, status="failed",
                    summary="app.install is not implemented in v1 (intentionally unsupported).",
                    output={"error": "not_implemented", "dry_run": proposal.dry_run},
                )
        except (ValueError, FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
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
        if proposal.capability == "app.launch":
            pid = result.output.get("pid")
            running = _pid_running(pid) if isinstance(pid, int) else False
            return {
                "ok": running or bool(proposal.dry_run),
                "checked": ["allowlist.match", "process.launched"],
                "pid_running": running,
                "mode": "real",
            }
        if proposal.capability == "app.focus":
            return {
                "ok": bool(result.output.get("focused")) or bool(proposal.dry_run),
                "checked": ["allowlist.match", "window.enumerated", "foreground.requested"],
                "mode": "real",
            }
        return {"ok": True, "checked": ["capability.supported"], "mode": "real"}

    # ------------------------------------------------------------------
    def _resolve_name(self, raw: Any) -> Tuple[str, str]:
        if not raw or not isinstance(raw, str):
            raise ValueError("Parameter 'name' is required (allowlisted short name).")
        key = raw.strip().lower()
        if key not in self._allowlist:
            raise ValueError(
                f"Application '{raw}' is not in the allowlist: {sorted(self._allowlist)}"
            )
        resolved = self._allowlist[key]
        if not resolved or not Path(resolved).exists():
            raise FileNotFoundError(f"Allowlisted app '{key}' has no executable on this machine.")
        return key, resolved

    def _execute_launch(self, proposal: ActionProposal) -> ActionResult:
        key, resolved = self._resolve_name(proposal.parameters.get("name"))

        # Optional string args list — keep strictly-typed, no shell expansion.
        args = proposal.parameters.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ValueError("Parameter 'args' must be a list of strings if provided.")

        if proposal.dry_run:
            return ActionResult(
                proposal=proposal, status="executed",
                summary=f"[dry-run] Would launch {key} ({resolved}) with args {args}.",
                output={"name": key, "executable": resolved, "args": args,
                        "pid": None, "dry_run": True},
            )

        creation_flags = 0
        if self._platform.startswith("win"):
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — keeps the child
            # independent of the Python process and prevents console inheritance.
            creation_flags = 0x00000008 | 0x00000200

        proc = subprocess.Popen(
            [resolved, *args],
            close_fds=True,
            creationflags=creation_flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return ActionResult(
            proposal=proposal, status="executed",
            summary=f"Launched {key} (pid={proc.pid}).",
            output={"name": key, "executable": resolved, "args": args,
                    "pid": proc.pid, "dry_run": False},
        )

    def _execute_focus(self, proposal: ActionProposal) -> ActionResult:
        key, resolved = self._resolve_name(proposal.parameters.get("name"))

        if proposal.dry_run:
            return ActionResult(
                proposal=proposal, status="executed",
                summary=f"[dry-run] Would focus running instance of {key} ({resolved}).",
                output={"name": key, "executable": resolved, "focused": False,
                        "hwnd": None, "pid": None, "dry_run": True},
            )
        if not self._platform.startswith("win"):
            return ActionResult(
                proposal=proposal, status="failed",
                summary=f"app.focus is only supported on Windows "
                        f"(detected {self._platform!r}).",
                output={"error": "platform_unsupported",
                        "platform": self._platform,
                        "dry_run": False},
            )

        matches = self._find_hwnds(resolved)
        if not matches:
            return ActionResult(
                proposal=proposal, status="failed",
                summary=f"No running window found for {key}. Launch it first.",
                output={"name": key, "executable": resolved,
                        "error": "not_running",
                        "focused": False, "dry_run": False},
            )
        hwnd, pid, exe = matches[0]
        focused = False
        try:
            focused = bool(self._focus_hwnd(hwnd))
        except OSError as exc:
            return ActionResult(
                proposal=proposal, status="failed",
                summary=f"app.focus failed for {key}: {exc}",
                output={"error": str(exc), "error_type": type(exc).__name__,
                        "name": key, "hwnd": hwnd, "pid": pid,
                        "dry_run": False},
            )
        return ActionResult(
            proposal=proposal,
            status="executed" if focused else "failed",
            summary=(f"Focused {key} (hwnd={hwnd}, pid={pid})."
                     if focused else
                     f"SetForegroundWindow refused by Windows for {key} "
                     f"(hwnd={hwnd}, pid={pid}). Windows restricts foreground "
                     "changes; click the target window once, or relaunch."),
            output={"name": key, "executable": exe, "hwnd": hwnd,
                    "pid": pid, "focused": focused, "dry_run": False,
                    **({"error": "set_foreground_refused"} if not focused else {})},
        )


def _pid_running(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        if sys.platform.startswith("win"):
            # Signal 0 works on POSIX; on Windows, use tasklist via subprocess as
            # a dep-free probe — but keep it cheap: treat any non-exception from
            # os.kill as alive. Windows raises OSError for bad handles.
            os.kill(pid, 0)
            return True
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False
