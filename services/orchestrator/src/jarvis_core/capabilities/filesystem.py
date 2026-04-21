"""Real filesystem capability adapter (Windows-first, stdlib only).

Capabilities:
  filesystem.read    - Tier 0. Read metadata + (small) preview of a file.
  filesystem.list    - Tier 0. List contents of a directory.
  filesystem.search  - Tier 0. Glob files under a scoped root.
  filesystem.write   - Tier 1. Write a text file within sandbox_root.
  filesystem.move    - Tier 2. Move within read_roots, destination within sandbox_root.

Scope safety
------------
- Reads / lists / searches may touch any path under `read_roots` (default:
  workspace_root + sandbox_root).
- Writes must resolve inside `sandbox_root`. Path traversal (..) is blocked.
- All paths are resolved and compared by absolute prefix.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models import ActionProposal, ActionResult
from .base import CapabilityAdapter

_SUPPORTED = {
    "filesystem.read",
    "filesystem.list",
    "filesystem.search",
    "filesystem.write",
    "filesystem.move",
}
_MAX_PREVIEW_BYTES = 8 * 1024
_MAX_LIST_ENTRIES = 500
_MAX_SEARCH_RESULTS = 200
_MAX_WRITE_BYTES = 1 * 1024 * 1024  # 1 MB per write


class ScopeError(ValueError):
    """Raised when a path escapes the configured scope."""


def _resolve_within(candidate: Path, roots: List[Path]) -> Path:
    """Resolve `candidate` and ensure it lies inside one of `roots`."""
    resolved = Path(candidate).resolve()
    for root in roots:
        root_resolved = Path(root).resolve()
        try:
            resolved.relative_to(root_resolved)
            return resolved
        except ValueError:
            continue
    allowed = ", ".join(str(Path(r).resolve()) for r in roots)
    raise ScopeError(f"Path {resolved} is outside allowed roots ({allowed}).")


class FilesystemCapability(CapabilityAdapter):
    name = "filesystem"

    def __init__(
        self,
        sandbox_root: Optional[Path] = None,
        read_roots: Optional[List[Path]] = None,
    ) -> None:
        self.sandbox_root = Path(sandbox_root).resolve() if sandbox_root else None
        self.read_roots: List[Path] = [Path(p).resolve() for p in (read_roots or [])]
        if self.sandbox_root and self.sandbox_root not in self.read_roots:
            self.read_roots.append(self.sandbox_root)

    # ------------------------------------------------------------------
    def supports(self, capability: str) -> bool:
        return capability in _SUPPORTED

    def execute(self, proposal: ActionProposal) -> ActionResult:
        try:
            if proposal.capability == "filesystem.read":
                return self._execute_read(proposal)
            if proposal.capability == "filesystem.list":
                return self._execute_list(proposal)
            if proposal.capability == "filesystem.search":
                return self._execute_search(proposal)
            if proposal.capability == "filesystem.write":
                return self._execute_write(proposal)
            if proposal.capability == "filesystem.move":
                return self._execute_move(proposal)
        except (ScopeError, ValueError, FileNotFoundError, PermissionError, OSError) as exc:
            return ActionResult(
                proposal=proposal,
                status="failed",
                summary=f"{proposal.capability} failed: {exc}",
                output={"error": str(exc), "error_type": type(exc).__name__,
                        "dry_run": proposal.dry_run},
            )
        raise KeyError(f"Unsupported capability: {proposal.capability}")

    def verify(self, proposal: ActionProposal, result: ActionResult) -> Dict[str, Any]:
        if result.status != "executed":
            return {"ok": False, "reason": result.status, "mode": "real"}

        if proposal.capability == "filesystem.write":
            path = result.output.get("path")
            exists = bool(path and Path(path).exists())
            return {
                "ok": exists or bool(proposal.dry_run),
                "checked": ["sandbox.contains", "file.exists"],
                "file_exists": exists,
                "mode": "real",
            }
        if proposal.capability == "filesystem.move":
            dest = result.output.get("destination")
            src = result.output.get("source")
            dest_ok = bool(dest and Path(dest).exists())
            src_gone = bool(src and not Path(src).exists())
            return {
                "ok": (dest_ok and src_gone) or bool(proposal.dry_run),
                "destination_exists": dest_ok,
                "source_removed": src_gone,
                "mode": "real",
            }
        return {"ok": True, "checked": ["path.in_scope"], "mode": "real"}

    # ------------------------------------------------------------------
    def _require_read_roots(self) -> List[Path]:
        if not self.read_roots:
            raise ScopeError("No read roots configured.")
        return self.read_roots

    def _require_sandbox(self) -> Path:
        if self.sandbox_root is None:
            raise ScopeError("No sandbox root configured; writes are disabled.")
        return self.sandbox_root

    def _execute_read(self, proposal: ActionProposal) -> ActionResult:
        raw = proposal.parameters.get("path")
        if not raw:
            raise ValueError("Parameter 'path' is required.")
        target = _resolve_within(Path(raw), self._require_read_roots())
        if not target.exists():
            raise FileNotFoundError(f"Path does not exist: {target}")
        if not target.is_file():
            raise ValueError(f"Path is not a file: {target}")

        stat = target.stat()
        preview: Optional[str] = None
        if stat.st_size <= _MAX_PREVIEW_BYTES:
            try:
                preview = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                preview = None

        if proposal.dry_run:
            return ActionResult(
                proposal=proposal, status="executed",
                summary=f"[dry-run] Would read {target} ({stat.st_size} bytes).",
                output={"path": str(target), "size": stat.st_size, "dry_run": True},
            )

        return ActionResult(
            proposal=proposal, status="executed",
            summary=f"Read metadata for {target} ({stat.st_size} bytes).",
            output={
                "path": str(target),
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
                "preview": preview,
                "preview_truncated": stat.st_size > _MAX_PREVIEW_BYTES,
                "dry_run": False,
            },
        )

    def _execute_list(self, proposal: ActionProposal) -> ActionResult:
        raw = proposal.parameters.get("path")
        if not raw:
            raise ValueError("Parameter 'path' is required.")
        target = _resolve_within(Path(raw), self._require_read_roots())
        if not target.exists():
            raise FileNotFoundError(f"Path does not exist: {target}")
        if not target.is_dir():
            raise ValueError(f"Path is not a directory: {target}")

        entries: List[Dict[str, Any]] = []
        for idx, child in enumerate(sorted(target.iterdir())):
            if idx >= _MAX_LIST_ENTRIES:
                break
            try:
                stat = child.stat()
                entries.append({
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": stat.st_size if child.is_file() else None,
                })
            except OSError:
                continue

        return ActionResult(
            proposal=proposal, status="executed",
            summary=f"Listed {len(entries)} entries under {target}.",
            output={"path": str(target), "count": len(entries), "entries": entries,
                    "truncated": len(entries) >= _MAX_LIST_ENTRIES,
                    "dry_run": proposal.dry_run},
        )

    def _execute_search(self, proposal: ActionProposal) -> ActionResult:
        raw_base = proposal.parameters.get("path")
        pattern = proposal.parameters.get("pattern")
        if not raw_base or not pattern:
            raise ValueError("Parameters 'path' and 'pattern' are required.")
        if not isinstance(pattern, str) or "/" in pattern or "\\" in pattern:
            raise ValueError("'pattern' must be a simple glob (no path separators).")
        base = _resolve_within(Path(raw_base), self._require_read_roots())
        if not base.is_dir():
            raise ValueError(f"Search base is not a directory: {base}")

        matches: List[str] = []
        for root, _dirs, files in os.walk(base):
            for name in files:
                if fnmatch.fnmatch(name, pattern):
                    matches.append(str(Path(root) / name))
                    if len(matches) >= _MAX_SEARCH_RESULTS:
                        break
            if len(matches) >= _MAX_SEARCH_RESULTS:
                break

        return ActionResult(
            proposal=proposal, status="executed",
            summary=f"Search matched {len(matches)} files for '{pattern}' under {base}.",
            output={"path": str(base), "pattern": pattern, "matches": matches,
                    "truncated": len(matches) >= _MAX_SEARCH_RESULTS,
                    "dry_run": proposal.dry_run},
        )

    def _execute_write(self, proposal: ActionProposal) -> ActionResult:
        sandbox = self._require_sandbox()
        raw = proposal.parameters.get("path")
        content = proposal.parameters.get("content", "")
        if not raw:
            raise ValueError("Parameter 'path' is required.")
        if not isinstance(content, str):
            raise ValueError("Parameter 'content' must be a string.")
        if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
            raise ValueError(f"Content exceeds {_MAX_WRITE_BYTES} byte write cap.")

        target = _resolve_within(Path(raw), [sandbox])
        if proposal.dry_run:
            return ActionResult(
                proposal=proposal, status="executed",
                summary=f"[dry-run] Would write {len(content)} chars to {target}.",
                output={"path": str(target), "size": len(content), "dry_run": True},
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ActionResult(
            proposal=proposal, status="executed",
            summary=f"Wrote {len(content)} chars to {target}.",
            output={"path": str(target), "size": target.stat().st_size, "dry_run": False},
        )

    def _execute_move(self, proposal: ActionProposal) -> ActionResult:
        sandbox = self._require_sandbox()
        source_raw = proposal.parameters.get("source")
        dest_raw = proposal.parameters.get("destination")
        if not source_raw or not dest_raw:
            raise ValueError("Parameters 'source' and 'destination' are required.")
        source = _resolve_within(Path(source_raw), self._require_read_roots())
        dest = _resolve_within(Path(dest_raw), [sandbox])
        if not source.exists():
            raise FileNotFoundError(f"Source does not exist: {source}")

        if proposal.dry_run:
            return ActionResult(
                proposal=proposal, status="executed",
                summary=f"[dry-run] Would move {source} -> {dest}.",
                output={"source": str(source), "destination": str(dest), "dry_run": True},
            )

        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(dest))
        return ActionResult(
            proposal=proposal, status="executed",
            summary=f"Moved {source} -> {dest}.",
            output={"source": str(source), "destination": str(dest), "dry_run": False},
        )
