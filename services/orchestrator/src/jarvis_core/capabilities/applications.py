"""Real application capability adapter (Windows-first, stdlib only).

Capabilities:
  app.launch   - Tier 1. Launch an allowlisted Windows app by short name.
  app.focus    - Tier 1. Same as launch for v1 (Windows raises foreground on new process).
  app.install  - Tier 2. Not implemented — always returns failed with a clear reason,
                 but policy still routes through approval first.

Allowlist-only: `app.launch` never accepts arbitrary executable paths. The
allowlist maps friendly names to resolved binaries (e.g. "notepad" ->
C:\\Windows\\System32\\notepad.exe).  If a binary is missing, the call fails
gracefully rather than silently doing nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

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


class ApplicationCapability(CapabilityAdapter):
    name = "applications"

    def __init__(self, allowlist: Optional[Dict[str, str]] = None) -> None:
        self._allowlist = _resolve_allowlist(allowlist)

    # ------------------------------------------------------------------
    def supports(self, capability: str) -> bool:
        return capability in _SUPPORTED

    def execute(self, proposal: ActionProposal) -> ActionResult:
        try:
            if proposal.capability in ("app.launch", "app.focus"):
                return self._execute_launch(proposal)
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
        if proposal.capability in ("app.launch", "app.focus"):
            pid = result.output.get("pid")
            running = _pid_running(pid) if isinstance(pid, int) else False
            return {
                "ok": running or bool(proposal.dry_run),
                "checked": ["allowlist.match", "process.launched"],
                "pid_running": running,
                "mode": "real",
            }
        return {"ok": True, "checked": ["capability.supported"], "mode": "real"}

    # ------------------------------------------------------------------
    def _execute_launch(self, proposal: ActionProposal) -> ActionResult:
        raw = proposal.parameters.get("name")
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
        if sys.platform.startswith("win"):
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
