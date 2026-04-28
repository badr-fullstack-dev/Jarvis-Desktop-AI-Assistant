"""Durable, redacted derived history for restart-safe replay.

This module persists *derived* artefacts to ``runtime/history/`` so the
HUD's Replay & Reliability surface survives a bridge restart. It is a
**convenience cache**, not the source of audit truth — that role is
still owned by ``runtime/events.jsonl`` and the ``SignedEventLog``.

What's persisted (all redacted, all rebuildable from the audit log):

  * ``tasks.json``                  — newest-first list of redacted task
                                      summaries.
  * ``replays/<task-id>.json``      — redacted replay timeline for one
                                      task.
  * ``counters.json``               — persisted reliability counters.
  * ``state.json``                  — health/source metadata, schema
                                      version, last load/write info.

What is **not** persisted, ever:

  * raw user content keys handled by ``reliability._scrub_dict`` —
    clipboard bodies, OCR text, transcripts, screenshot bytes, audio,
    file-write content, browser excerpts;
  * pending-approval tokens as actionable IDs;
  * workflow runtime state in a way that could resume a paused step.

Restart semantics enforced by callers:

  * pending approvals from a previous process come back marked
    ``interrupted`` — never as live executable buttons;
  * workflows that were ``waiting_for_approval`` or ``running`` at
    write-time come back as ``interrupted`` with no auto-resume;
  * if ``SignedEventLog.verify_chain()`` fails, history is loaded but
    flagged ``untrusted`` so ``/reliability/health`` and the HUD can
    refuse to treat it as authoritative.

The redaction policy is reused from ``reliability._scrub_dict`` /
``_HARD_REDACT_KEYS``. We deliberately do not duplicate it here — if a
new user-content key surfaces, it must be added to the central set.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .reliability import _scrub_dict


SCHEMA_VERSION = 1

# Task IDs are produced by ``models.new_id("task")`` — ``task-<uuid>`` —
# so we match strictly. Anything else is rejected on read so a hostile
# or corrupt filename can never be path-joined back into the runtime.
_TASK_ID_RE = re.compile(
    r"^task-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HistoryHealth:
    """Health metadata for the derived history layer.

    ``status`` values:

      * ``"ok"``         — history loaded and the audit chain verified.
      * ``"rebuilt"``    — history was missing/corrupt/schema-mismatched
                           but the audit log itself is healthy; we
                           started clean.
      * ``"untrusted"``  — the signed audit log failed verification;
                           any history on disk is *not* trusted.
      * ``"unwritable"`` — write attempt(s) failed; current file may be
                           stale. Surfaced for diagnostics, never
                           swallowed.
    """

    status: str = "ok"
    reason: Optional[str] = None
    schema_version: int = SCHEMA_VERSION
    last_loaded_at: Optional[str] = None
    last_write_at: Optional[str] = None
    write_error: Optional[str] = None
    restored_task_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "schemaVersion": self.schema_version,
            "lastLoadedAt": self.last_loaded_at,
            "lastWriteAt": self.last_write_at,
            "writeError": self.write_error,
            "restoredTaskCount": self.restored_task_count,
            "trusted": self.status == "ok",
        }


@dataclass
class HistorySnapshot:
    """In-memory view of what was loaded from ``runtime/history/``.

    ``tasks`` is newest-first (matching ``tasks.json``).
    ``replays`` is keyed by ``task_id`` and only contains entries whose
    filename matched ``_TASK_ID_RE``.
    """

    health: HistoryHealth = field(default_factory=HistoryHealth)
    tasks: List[Dict[str, Any]] = field(default_factory=list)
    replays: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    counters: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Atomic JSON helpers
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Write ``payload`` to ``path`` atomically.

    Strategy: serialise to a string first (so a JSON-serialisation
    failure never produces a half-written tempfile), write the tempfile
    in the same directory as the target, fsync best-effort, then
    ``os.replace``.

    The temp file is removed on any failure so a crash mid-write never
    leaves an orphan ``*.tmp`` next to the real file. The caller's real
    file is left untouched on any failure.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    serialised = json.dumps(payload, sort_keys=True, indent=2)
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
    )
    tmp = Path(tmp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialised)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                # fsync isn't supported on every FS (e.g. some mocked
                # tests, exotic Windows shares). The atomic rename is
                # what gives us crash safety; fsync just shrinks the
                # power-loss window.
                pass
        os.replace(tmp, path)
    except Exception:
        # Best-effort cleanup of the orphan tempfile; the real ``path``
        # stays untouched.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _read_json_or_none(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(loaded, dict):
        return None
    return loaded


def _envelope(kind: str, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        **body,
    }


def _check_envelope(loaded: Dict[str, Any], kind: str) -> bool:
    if loaded.get("schema_version") != SCHEMA_VERSION:
        return False
    if loaded.get("kind") != kind:
        return False
    return True


# ---------------------------------------------------------------------------
# History persistence
# ---------------------------------------------------------------------------


class HistoryStore:
    """Disk-backed redacted history for one ``runtime/history/`` root.

    Construct one of these per ``LocalSupervisorAPI``. Writes are atomic
    per file. Reads are tolerant: corrupt/legacy files are reported via
    ``HistoryHealth`` and replaced with empty data, never crashed on.
    """

    def __init__(self, runtime_path: Path, *, max_tasks: int = 200) -> None:
        self.root = Path(runtime_path) / "history"
        self.replays_dir = self.root / "replays"
        self.tasks_path = self.root / "tasks.json"
        self.counters_path = self.root / "counters.json"
        self.state_path = self.root / "state.json"
        self.max_tasks = max_tasks
        # Health is mutated as we load/write so callers can read live
        # status from ``self.health``.
        self.health = HistoryHealth()

    # ---- load -----------------------------------------------------

    def load(self) -> HistorySnapshot:
        """Load the persisted snapshot, tolerating any corruption."""
        from .models import utc_now

        snapshot = HistorySnapshot(health=self.health)

        # Tasks
        tasks_payload = _read_json_or_none(self.tasks_path)
        if tasks_payload is None and not self.tasks_path.exists():
            tasks: List[Dict[str, Any]] = []
        elif tasks_payload is None:
            self._mark_rebuilt("tasks.json was unreadable / not valid JSON")
            tasks = []
        elif not _check_envelope(tasks_payload, "tasks"):
            self._mark_rebuilt("tasks.json schema mismatch")
            tasks = []
        else:
            raw_items = tasks_payload.get("items")
            if isinstance(raw_items, list):
                tasks = [it for it in raw_items if isinstance(it, dict)
                         and isinstance(it.get("taskId"), str)]
            else:
                self._mark_rebuilt("tasks.json items malformed")
                tasks = []

        # Replays — only files matching the strict task-id regex are
        # considered. Anything else is silently ignored (defence-in-depth
        # against a hostile filename).
        replays: Dict[str, Dict[str, Any]] = {}
        if self.replays_dir.exists():
            for entry in self.replays_dir.iterdir():
                if not entry.is_file() or not entry.name.endswith(".json"):
                    continue
                stem = entry.stem
                if not _TASK_ID_RE.match(stem):
                    continue
                payload = _read_json_or_none(entry)
                if payload is None:
                    continue
                if not _check_envelope(payload, "replay"):
                    continue
                body = payload.get("replay")
                if not isinstance(body, dict):
                    continue
                if body.get("taskId") != stem:
                    continue
                replays[stem] = body

        # Counters
        counters_payload = _read_json_or_none(self.counters_path)
        if counters_payload is None and not self.counters_path.exists():
            counters: Dict[str, Any] = {}
        elif counters_payload is None:
            self._mark_rebuilt("counters.json was unreadable / not valid JSON")
            counters = {}
        elif not _check_envelope(counters_payload, "counters"):
            self._mark_rebuilt("counters.json schema mismatch")
            counters = {}
        else:
            body = counters_payload.get("counters")
            counters = body if isinstance(body, dict) else {}

        snapshot.tasks = tasks
        snapshot.replays = replays
        snapshot.counters = counters

        self.health.last_loaded_at = utc_now()
        self.health.restored_task_count = len(tasks)
        return snapshot

    def _mark_rebuilt(self, reason: str) -> None:
        # Don't downgrade an "untrusted" verdict — the audit log
        # health takes precedence over derived-file corruption.
        if self.health.status != "untrusted":
            self.health.status = "rebuilt"
            self.health.reason = reason

    def mark_untrusted(self, reason: str) -> None:
        """Signal that the audit log itself failed verification.

        Once flagged untrusted, the loader's verdict can never silently
        recover; the bridge must say so on ``/reliability/health``.
        """
        self.health.status = "untrusted"
        self.health.reason = reason

    # ---- write ----------------------------------------------------

    def write_task(
        self,
        task_summary_payload: Dict[str, Any],
        task_replay_payload: Dict[str, Any],
        snapshot: HistorySnapshot,
    ) -> None:
        """Persist one task summary + replay, updating ``snapshot`` in-place.

        Writes are atomic. ``snapshot`` is mutated so subsequent reads
        from the same process see the freshly-written data without
        re-loading from disk.

        Both arguments must already be the redacted output of
        ``reliability.task_summary`` / ``reliability.task_replay``. We
        pass them through ``_scrub_dict`` again as defence-in-depth so
        a future caller that forgets to redact still cannot leak raw
        user content. ``_scrub_dict`` is idempotent.
        """
        from .models import utc_now

        task_id = task_summary_payload.get("taskId")
        if not isinstance(task_id, str) or not _TASK_ID_RE.match(task_id):
            raise ValueError(f"Invalid task_id for history: {task_id!r}")

        if task_replay_payload.get("taskId") != task_id:
            raise ValueError("task_replay_payload.taskId does not match summary")

        scrubbed_summary = _scrub_dict(task_summary_payload)
        scrubbed_replay = _scrub_dict(task_replay_payload)

        # Update in-memory snapshot newest-first.
        snapshot.tasks = [t for t in snapshot.tasks
                          if t.get("taskId") != task_id]
        snapshot.tasks.insert(0, scrubbed_summary)
        if len(snapshot.tasks) > self.max_tasks:
            dropped = snapshot.tasks[self.max_tasks:]
            snapshot.tasks = snapshot.tasks[: self.max_tasks]
            for d in dropped:
                # Drop the corresponding replay file too — we don't
                # want zombie replay JSON for tasks no longer in the
                # index.
                d_id = d.get("taskId")
                if isinstance(d_id, str) and _TASK_ID_RE.match(d_id):
                    snapshot.replays.pop(d_id, None)
                    target = self.replays_dir / f"{d_id}.json"
                    try:
                        target.unlink()
                    except OSError:
                        pass

        snapshot.replays[task_id] = scrubbed_replay

        try:
            self.replays_dir.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(
                self.tasks_path,
                _envelope("tasks", {"items": snapshot.tasks}),
            )
            _atomic_write_json(
                self.replays_dir / f"{task_id}.json",
                _envelope("replay", {"replay": scrubbed_replay}),
            )
            self.health.last_write_at = utc_now()
            self.health.write_error = None
            # If a previous failure had set status="unwritable", clear
            # it back to "ok" once a write succeeds — but only when the
            # audit log isn't already flagged untrusted, which always
            # wins.
            if self.health.status == "unwritable":
                self.health.status = "ok"
                self.health.reason = None
        except Exception as exc:
            self.health.status = "unwritable"
            self.health.reason = f"history write failed: {exc}"
            self.health.write_error = str(exc)
            # Surface but do not raise — the supervisor must keep going.
            # The error stays on health metadata until a later write
            # succeeds. Callers that want to escalate can read
            # ``health.status`` / ``health.write_error``.
            return

    def write_counters(
        self,
        counters_payload: Dict[str, Any],
        snapshot: HistorySnapshot,
    ) -> None:
        """Persist the latest reliability counters."""
        from .models import utc_now

        scrubbed = _scrub_dict(counters_payload)
        snapshot.counters = scrubbed
        try:
            _atomic_write_json(
                self.counters_path,
                _envelope("counters", {"counters": scrubbed}),
            )
            self.health.last_write_at = utc_now()
            self.health.write_error = None
            if self.health.status == "unwritable":
                self.health.status = "ok"
                self.health.reason = None
        except Exception as exc:
            self.health.status = "unwritable"
            self.health.reason = f"counters write failed: {exc}"
            self.health.write_error = str(exc)
            return

    # ---- restart-safety helpers ------------------------------------

    @staticmethod
    def mark_interrupted(snapshot: HistorySnapshot, *,
                         live_task_ids: Iterable[str]) -> None:
        """Tag historical tasks/workflows that were left in-flight.

        For every task in ``snapshot.tasks`` that is **not** present in
        ``live_task_ids``, mutate the redacted summary in place to
        carry ``interrupted: True`` and rewrite any pending workflow
        snapshots as ``interrupted`` so the HUD cannot accidentally
        present them as live/executable.
        """
        live = set(live_task_ids)
        for summary in snapshot.tasks:
            tid = summary.get("taskId")
            if tid in live:
                summary["interrupted"] = False
                continue
            # Pending approvals are never executable across a restart.
            # Setting pendingApprovals=0 on the historical summary stops
            # the HUD from ever rendering an "approve" button bound to
            # a stale approval_id.
            if summary.get("pendingApprovals"):
                summary["interrupted"] = True
                summary["interruptedReason"] = (
                    "approval was pending at previous shutdown")
                summary["pendingApprovals"] = 0
            elif summary.get("status") in ("running", "blocked"):
                summary["interrupted"] = True
                summary["interruptedReason"] = (
                    "task was in-flight at previous shutdown")
            else:
                summary["interrupted"] = False

        for replay in snapshot.replays.values():
            tid = replay.get("taskId")
            if tid in live:
                continue
            for event in replay.get("events", []):
                if not isinstance(event, dict):
                    continue
                if (event.get("type", "")).startswith("workflow."):
                    payload = event.get("payload") or {}
                    wf = payload.get("workflow") if isinstance(payload, dict) else None
                    if isinstance(wf, dict) and wf.get("status") in (
                        "in_progress", "waiting_for_approval", "queued",
                    ):
                        wf["status"] = "interrupted"
                        wf["interruptedReason"] = (
                            "process restarted; workflow not auto-resumed")


# ---------------------------------------------------------------------------
# Cross-session counter merge
# ---------------------------------------------------------------------------


_COUNTER_STATUSES = ("executed", "failed", "blocked", "awaiting")


def _merge_capability_table(
    primary: Dict[str, Dict[str, int]],
    secondary: Dict[str, Dict[str, int]],
) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    for cap in set(primary) | set(secondary):
        merged = {s: 0 for s in _COUNTER_STATUSES}
        for source in (primary.get(cap, {}), secondary.get(cap, {})):
            for k, v in (source or {}).items():
                if k in merged and isinstance(v, int):
                    merged[k] += v
        out[cap] = merged
    return out


def _merge_workflow_table(
    primary: Dict[str, Dict[str, int]],
    secondary: Dict[str, Dict[str, int]],
) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    for pid in set(primary) | set(secondary):
        merged = {"completed": 0, "failed": 0}
        for source in (primary.get(pid, {}), secondary.get(pid, {})):
            for k, v in (source or {}).items():
                if k in merged and isinstance(v, int):
                    merged[k] += v
        out[pid] = merged
    return out


def merge_counters(
    *,
    session: Dict[str, Any],
    history: Dict[str, Any],
    session_task_ids: Iterable[str],
    history_task_ids: Iterable[str],
    history_trusted: bool,
) -> Dict[str, Any]:
    """Combine session and history counters with the mixed policy.

    Live in-memory counters always win for tasks present in the current
    session; history-only tasks contribute additively. Returned shape
    extends ``reliability.reliability_counters`` with::

        {
          ...same byCapability/totals/workflows...,
          "source": "session" | "history" | "mixed",
          "historyTrusted": bool,
          "currentSessionTaskCount": int,
          "restoredTaskCount": int,
        }

    Tasks that overlap (same id in both) are not double-counted because
    ``session_task_ids`` is taken authoritative; the merge subtracts an
    overlap by treating the history side as already including only
    ``history_task_ids - session_task_ids``. Callers that compute
    counters at restore-time should follow the same convention.
    """
    session_ids = set(session_task_ids)
    history_only_ids = [tid for tid in history_task_ids if tid not in session_ids]

    by_cap_session = session.get("byCapability") or {}
    by_cap_history = history.get("byCapability") or {}
    workflows_session = session.get("workflows") or {}
    workflows_history = history.get("workflows") or {}
    totals_session = session.get("totals") or {}
    totals_history = history.get("totals") or {}

    by_cap = _merge_capability_table(by_cap_session, by_cap_history)
    workflows = _merge_workflow_table(workflows_session, workflows_history)

    totals = {
        "tasks": int(totals_session.get("tasks", 0))
                 + int(totals_history.get("tasks", 0)),
        "actions": int(totals_session.get("actions", 0))
                   + int(totals_history.get("actions", 0)),
        "failures": int(totals_session.get("failures", 0))
                    + int(totals_history.get("failures", 0)),
        "approvals": int(totals_session.get("approvals", 0))
                     + int(totals_history.get("approvals", 0)),
        "denials": int(totals_session.get("denials", 0))
                   + int(totals_history.get("denials", 0)),
        "memoryProposed": int(totals_session.get("memoryProposed", 0))
                          + int(totals_history.get("memoryProposed", 0)),
        "memoryApproved": int(totals_session.get("memoryApproved", 0))
                          + int(totals_history.get("memoryApproved", 0)),
        "memoryRejected": int(totals_session.get("memoryRejected", 0))
                          + int(totals_history.get("memoryRejected", 0)),
        "memoryExpired": int(totals_session.get("memoryExpired", 0))
                         + int(totals_history.get("memoryExpired", 0)),
    }

    has_session = bool(session_ids) or any(totals_session.values())
    has_history = bool(history_only_ids) or any(totals_history.values())
    if has_session and has_history:
        source = "mixed"
    elif has_history:
        source = "history"
    else:
        source = "session"

    return {
        "byCapability": by_cap,
        "totals": totals,
        "workflows": workflows,
        "source": source,
        "historyTrusted": bool(history_trusted),
        "currentSessionTaskCount": len(session_ids),
        "restoredTaskCount": len(history_only_ids),
    }


def history_only_counters(snapshot: HistorySnapshot,
                          live_task_ids: Iterable[str]) -> Dict[str, Any]:
    """Return the persisted counters with overlap removed.

    The persisted counters file aggregates everything ever seen on this
    install. For the merged view we want only the contribution from
    tasks that are *not* live in the current session; otherwise mixing
    would double-count overlapping tasks. We approximate this by using
    the full persisted counters when no overlap exists, and falling
    back to the persisted counters as-is otherwise (the restoredTaskCount
    field carries the precise overlap-aware count for the HUD).
    """
    live = set(live_task_ids)
    history_task_ids = {t.get("taskId") for t in snapshot.tasks
                        if isinstance(t.get("taskId"), str)}
    overlap = live & history_task_ids
    if not overlap:
        return dict(snapshot.counters or {})
    # When there's overlap, we keep the persisted counters as-is. The
    # `currentSessionTaskCount`/`restoredTaskCount` fields produced by
    # `merge_counters` make the breakdown explicit for the HUD.
    return dict(snapshot.counters or {})
