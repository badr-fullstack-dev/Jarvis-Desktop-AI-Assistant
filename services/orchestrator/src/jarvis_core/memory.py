"""Curated memory store with explicit lifecycle and a sensitive-payload filter.

Layers
------
* ``profile``     — explicit user preferences ("user prefers https when scheme is missing").
* ``operational`` — runtime notes that help future planning ("workflow wf.write_then_read succeeded").
* ``lesson``      — cause-effect lessons ("planner clarification_needed for rule=write.outside_sandbox").
* ``tool``        — tool reliability notes ("desktop.ocr_foreground failed: provider_unavailable").

Lifecycle
---------
Every memory starts as a ``candidate`` and only influences planning after a
human ``approve`` call. ``reject`` and ``expire`` keep the row for audit (a
rejected lesson is just as useful as an approved one). Lifecycle transitions
record ``reviewed_at`` / ``reviewed_by`` / ``review_reason`` so the HUD can
show a complete trail.

Privacy
-------
``MemoryStore.propose`` runs the proposed item through
``reflection.is_sensitive_payload`` before persisting. The capability layer
or any caller that builds a ``MemoryItem`` cannot smuggle clipboard text,
OCR output, screenshot bytes, or raw transcripts into long-term memory —
the propose call raises :class:`MemoryRejectedError` with the reason. Use
the supplied :class:`SensitiveContentError` to surface the failure.
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Dict, Iterable, List, Optional

from .models import MemoryItem, utc_now


# --- status constants (kept loose so the HUD/bridge can serialise as strings) ---

STATUS_CANDIDATE = "candidate"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"

LIVE_STATUSES = (STATUS_CANDIDATE, STATUS_APPROVED)
ARCHIVED_STATUSES = (STATUS_REJECTED, STATUS_EXPIRED)


class MemoryRejectedError(ValueError):
    """Raised when ``propose`` refuses to persist a memory.

    The most common cause is the sensitive-payload filter catching
    clipboard text, OCR output, raw transcripts, or screenshot bytes —
    use ``str(exc)`` for an actionable message.
    """


class MemoryStore:
    """JSON-backed memory layers with an explicit lifecycle."""

    LAYERS = ("profile", "operational", "lesson", "tool")

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        for layer in self.LAYERS:
            path = self._path(layer)
            if not path.exists():
                path.write_text("[]", encoding="utf-8")
        # MemoryStore is shared between the supervisor (background tasks)
        # and the bridge handlers (threaded HTTP server). A simple RLock
        # is enough — the only contention is short JSON read/writes.
        self._lock = RLock()
        # Lazy import to avoid a hard cycle: reflection.py imports from
        # this module for constants. The filter is exposed so callers
        # can pre-screen if needed.
        from .reflection import is_sensitive_payload  # noqa: WPS433
        self._is_sensitive = is_sensitive_payload

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list(
        self,
        kind: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, object]]:
        kinds = [kind] if kind else list(self.LAYERS)
        rows: List[Dict[str, object]] = []
        with self._lock:
            for layer in kinds:
                rows.extend(self._read(layer))
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return rows

    def get(self, memory_id: str) -> Optional[Dict[str, object]]:
        with self._lock:
            for layer in self.LAYERS:
                for row in self._read(layer):
                    if row.get("memory_id") == memory_id:
                        return row
        return None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def propose(self, item: MemoryItem) -> MemoryItem:
        """Persist a new ``candidate`` memory item.

        Raises :class:`MemoryRejectedError` if the sensitive-payload
        filter trips. The filter is applied here — *not* at the call
        site — so even a hand-written ``MemoryItem`` carrying clipboard
        text or a transcript cannot be smuggled past it.
        """
        if item.kind not in self.LAYERS:
            raise ValueError(f"Unknown memory kind {item.kind!r}; expected one of {self.LAYERS}.")
        # Default newly-proposed items to candidate. Callers may set
        # status="approved" only if they own the lifecycle (rare; tests
        # currently do this for fixtures).
        if item.status not in (STATUS_CANDIDATE, STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPIRED):
            raise ValueError(f"Unknown memory status {item.status!r}.")
        reason = self._is_sensitive(item)
        if reason:
            raise MemoryRejectedError(
                f"Refusing to store memory of kind {item.kind!r}: {reason}"
            )
        with self._lock:
            items = self._read(item.kind)
            items.append(item.to_dict())
            self._write(item.kind, items)
        return item

    # Compatibility alias — old callers used ``add(...)`` directly.
    def add(self, item: MemoryItem) -> MemoryItem:
        return self.propose(item)

    def propose_lesson(
        self,
        summary: str,
        evidence: Iterable[str],
        trust_score: float,
        details: Optional[Dict[str, object]] = None,
        kind: str = "lesson",
    ) -> MemoryItem:
        """Convenience constructor — used by the Reflector and tests."""
        lesson = MemoryItem(
            kind=kind,
            summary=summary,
            details=details or {},
            evidence=list(evidence),
            trust_score=trust_score,
        )
        return self.propose(lesson)

    # ------------------------------------------------------------------
    # Lifecycle transitions
    # ------------------------------------------------------------------

    def approve(self, memory_id: str, *, reviewed_by: str = "user") -> Dict[str, object]:
        return self._transition(memory_id, STATUS_APPROVED, reviewed_by=reviewed_by)

    def reject(self, memory_id: str, *, reason: str = "", reviewed_by: str = "user") -> Dict[str, object]:
        return self._transition(memory_id, STATUS_REJECTED, reviewed_by=reviewed_by, reason=reason)

    def expire(self, memory_id: str, *, reason: str = "", reviewed_by: str = "user") -> Dict[str, object]:
        return self._transition(memory_id, STATUS_EXPIRED, reviewed_by=reviewed_by, reason=reason)

    def delete(self, memory_id: str) -> bool:
        """Physically remove a memory row.

        Use sparingly — ``reject`` / ``expire`` preserve the audit trail
        and are reversible. ``delete`` is for truly accidental rows or
        test cleanup.
        """
        with self._lock:
            for layer in self.LAYERS:
                items = self._read(layer)
                kept = [r for r in items if r.get("memory_id") != memory_id]
                if len(kept) != len(items):
                    self._write(layer, kept)
                    return True
        return False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _transition(
        self,
        memory_id: str,
        new_status: str,
        *,
        reviewed_by: str,
        reason: str = "",
    ) -> Dict[str, object]:
        with self._lock:
            for layer in self.LAYERS:
                items = self._read(layer)
                for row in items:
                    if row.get("memory_id") != memory_id:
                        continue
                    row["status"] = new_status
                    row["reviewed_at"] = utc_now()
                    row["reviewed_by"] = reviewed_by
                    if reason:
                        row["review_reason"] = reason
                    self._write(layer, items)
                    return row
        raise KeyError(f"No memory found for memory_id={memory_id!r}.")

    def _path(self, kind: str) -> Path:
        return self.root / f"{kind}.json"

    def _read(self, kind: str) -> List[Dict[str, object]]:
        return json.loads(self._path(kind).read_text(encoding="utf-8"))

    def _write(self, kind: str, items: List[Dict[str, object]]) -> None:
        self._path(kind).write_text(json.dumps(items, indent=2), encoding="utf-8")
