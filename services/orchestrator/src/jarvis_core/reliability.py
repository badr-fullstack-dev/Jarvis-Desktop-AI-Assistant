"""Replay, reliability, and event-log health summarisation.

This module is **read-only** over the existing task traces and signed
event log. It produces three things the HUD and local reviewers need:

1. :func:`task_summary` / :func:`recent_task_summaries` — a compact,
   redacted rollup per task suitable for a list view.
2. :func:`task_replay` — a redacted, ordered event timeline for a
   single task, used by the HUD's replay panel and any local debugger.
3. :func:`reliability_counters` — by-capability success/failure counts
   plus aggregate totals across all tasks.

A fourth helper, :func:`event_log_health`, wraps
:meth:`SignedEventLog.verify_chain` and reports the chain integrity
status without mutating the log.

Privacy
-------
Replay summaries are produced with a strict redaction pass:

* user-content keys (``text``, ``transcript``, ``raw_text``,
  ``raw_audio``, ``audio``, ``audio_base64``, ``ocr_text``,
  ``clipboard``, ``screenshot_bytes``, ``png_bytes``, ``content``,
  ``excerpt``, ``preview``, ``snippets``) are stripped from outputs and
  parameters and replaced with a small ``"<redacted: N bytes>"`` marker;
* line/word lists (``lines``, ``words``) are replaced with their
  length;
* free-form summaries are clipped to 200 characters;
* event payloads coming from ``plan.evaluated``, ``approval.requested``,
  ``approval.denied``, ``workflow.*``, ``lesson.proposed``,
  ``memory.*``, and the action lifecycle keep capability names,
  statuses, IDs, error types, verification keys, and timestamps but
  drop user-content bodies.

There is no LLM here. Summaries are deterministic and trace-driven so
that replay output is stable across runs.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

from .event_log import SignedEventLog
from .models import TaskRecord, TaskStatus


# ---------------------------------------------------------------------------
# Redaction primitives
# ---------------------------------------------------------------------------

# Keys whose VALUES are user content bodies. The redactor replaces a
# non-empty string/bytes value with a "<redacted: N bytes>" marker.
# All entries are stored lowercase; comparisons normalise the key.
_HARD_REDACT_KEYS = {
    "text", "transcript", "raw_text", "raw_audio", "audio",
    "audio_base64", "ocr_text", "clipboard", "screenshot_bytes",
    "png_bytes", "content", "excerpt", "preview", "snippets",
    "text_excerpt", "textexcerpt",
}

# Lists where we want to keep the count but not the entries.
_SUMMARIZE_LIST_KEYS = {"lines", "words"}

# Hard cap on free-form summary text in any replay event.
_MAX_SUMMARY_CHARS = 200
_MAX_OBJECTIVE_CHARS = 200


def _redact_value(value: Any) -> Any:
    """Return a privacy-safe rendering of ``value`` for replay output.

    Idempotent: if ``value`` is already a redaction marker, return it
    unchanged so a second pass through ``_scrub_dict`` does not produce
    "<redacted: N chars>" → "<redacted: 19 chars>" → ...
    """
    if isinstance(value, (bytes, bytearray)):
        return f"<redacted: {len(value)} bytes>"
    if isinstance(value, str):
        if not value:
            return ""
        if value.startswith("<redacted:"):
            return value
        return f"<redacted: {len(value)} chars>"
    return value


def _scrub_dict(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Deep-copy ``payload`` with sensitive keys redacted.

    Lists are walked so a nested ``{"output": {"lines": [...]}}`` shape
    is handled. The function is idempotent — calling it twice produces
    the same value as calling it once.
    """
    if not isinstance(payload, dict):
        return {}
    out: Dict[str, Any] = {}
    for key, value in payload.items():
        kl = str(key).lower()
        if kl in _HARD_REDACT_KEYS:
            out[key] = _redact_value(value)
            continue
        if kl in _SUMMARIZE_LIST_KEYS and isinstance(value, list):
            out[key] = {"count": len(value)}
            continue
        if isinstance(value, dict):
            out[key] = _scrub_dict(value)
        elif isinstance(value, list):
            out[key] = [_scrub_dict(v) if isinstance(v, dict) else v
                        for v in value]
        else:
            out[key] = value
    return out


def _clip(text: Optional[str], limit: int = _MAX_SUMMARY_CHARS) -> str:
    if not text:
        return ""
    text = text.replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# Replay timeline
# ---------------------------------------------------------------------------


def _action_status(entry: Dict[str, Any]) -> Optional[str]:
    result = (entry.get("result") or {}) if isinstance(entry, dict) else {}
    status = result.get("status")
    return status if isinstance(status, str) and status else None


def _action_capability(entry: Dict[str, Any]) -> Optional[str]:
    result = (entry.get("result") or {})
    proposal = (result.get("proposal") or {}) if isinstance(result, dict) else {}
    cap = proposal.get("capability")
    return cap if isinstance(cap, str) and cap else None


def _action_error_type(entry: Dict[str, Any]) -> Optional[str]:
    result = (entry.get("result") or {})
    output = (result.get("output") or {}) if isinstance(result, dict) else {}
    et = output.get("error_type") or output.get("error")
    if isinstance(et, str):
        return et[:120]
    return None


def _verification_ok(entry: Dict[str, Any]) -> Optional[bool]:
    result = (entry.get("result") or {})
    if not isinstance(result, dict):
        return None
    verification = result.get("verification") or {}
    if not isinstance(verification, dict) or "ok" not in verification:
        return None
    return bool(verification.get("ok"))


def _summarize_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Convert one trace entry into a redacted replay event.

    Each replay event carries: ``index``, ``timestamp``, ``type``,
    ``capability``, ``status``, ``summary``, ``errorType``,
    ``verificationOk``, ``payload`` (a scrubbed deep copy minus the
    obvious user-content keys). The original trace entry is never
    mutated; we always read it.
    """
    event_type = (entry.get("event") or "").strip()
    payload = _scrub_dict(entry)
    # Pull a short, human-readable summary depending on the event type.
    summary = ""
    if event_type == "action.executed":
        result = entry.get("result") or {}
        summary = result.get("summary") or ""
    elif event_type == "action.blocked":
        result = entry.get("result") or {}
        decision = (result.get("decision") or {}) if isinstance(result, dict) else {}
        summary = decision.get("reason") or result.get("summary") or "blocked"
    elif event_type == "approval.requested":
        appr = entry.get("approval") or {}
        cap = appr.get("capability", "")
        tier = appr.get("risk_tier", "?")
        summary = f"approval required for {cap} (tier {tier})"
    elif event_type == "approval.denied":
        appr = entry.get("approval") or {}
        summary = f"denied: {appr.get('reason') or 'no reason given'}"
    elif event_type == "plan.evaluated":
        plan = entry.get("plan") or {}
        if plan.get("status") == "mapped":
            summary = f"planner → {plan.get('capability')} ({plan.get('matchedRule')})"
        elif plan.get("status") == "clarification_needed":
            summary = f"planner clarification: {plan.get('matchedRule') or 'unknown_rule'}"
        else:
            summary = "planner: unsupported"
    elif event_type.startswith("workflow."):
        wf = entry.get("workflow") or {}
        suffix = event_type.split(".", 1)[1] if "." in event_type else event_type
        cur = wf.get("currentStep", 0)
        steps = wf.get("steps") or []
        summary = (f"workflow {wf.get('patternId', '?')} → {suffix} "
                   f"(step {cur + 1}/{len(steps)})")
    elif event_type == "lesson.proposed":
        memory = entry.get("memory") or {}
        summary = f"reflection proposed {memory.get('kind', 'memory')} candidate"
    elif event_type.startswith("memory."):
        memory = entry.get("memory") or {}
        summary = f"{event_type.split('.', 1)[1]}: {memory.get('kind', '?')}"
    else:
        summary = entry.get("summary") or event_type

    return {
        "index": 0,  # filled in by caller
        "timestamp": entry.get("timestamp") or entry.get("time") or "",
        "type": event_type,
        "capability": _action_capability(entry),
        "status": _action_status(entry),
        "summary": _clip(summary),
        "errorType": _action_error_type(entry),
        "verificationOk": _verification_ok(entry),
        "payload": payload,
    }


def task_replay(task: TaskRecord) -> Dict[str, Any]:
    """Return a redacted replay timeline for a single task.

    The output shape is stable: ``{"taskId", "objective", "status",
    "createdAt", "updatedAt", "events": [...]}``. The objective is
    capped at 200 chars; each event is produced by :func:`_summarize_entry`.
    """
    events: List[Dict[str, Any]] = []
    for index, entry in enumerate(task.trace or []):
        if not isinstance(entry, dict):
            continue
        e = _summarize_entry(entry)
        e["index"] = index
        events.append(e)
    status = task.status.value if isinstance(task.status, TaskStatus) else str(task.status)
    return {
        "taskId": task.task_id,
        "objective": _clip(task.objective, _MAX_OBJECTIVE_CHARS),
        "source": task.source or "",
        "status": status,
        "createdAt": task.created_at,
        "updatedAt": task.updated_at,
        "events": events,
    }


# ---------------------------------------------------------------------------
# Task summaries (compact list view)
# ---------------------------------------------------------------------------


def task_summary(task: TaskRecord) -> Dict[str, Any]:
    """Compact rollup per task — used for the recent-tasks list view."""
    actions = 0
    failures = 0
    approvals = 0
    denials = 0
    workflows: List[str] = []
    last_capability: Optional[str] = None

    for entry in (task.trace or []):
        if not isinstance(entry, dict):
            continue
        event = entry.get("event") or ""
        if event == "action.executed":
            actions += 1
            cap = _action_capability(entry)
            if cap:
                last_capability = cap
            if _action_status(entry) == "failed":
                failures += 1
        elif event == "action.blocked":
            actions += 1
            failures += 1
        elif event == "approval.requested":
            approvals += 1
        elif event == "approval.denied":
            denials += 1
        elif event == "workflow.completed":
            wf = entry.get("workflow") or {}
            pid = wf.get("patternId")
            if pid:
                workflows.append(pid)

    status = task.status.value if isinstance(task.status, TaskStatus) else str(task.status)
    pending_approvals = len(task.approvals or [])
    return {
        "taskId": task.task_id,
        "objective": _clip(task.objective, _MAX_OBJECTIVE_CHARS),
        "source": task.source or "",
        "status": status,
        "createdAt": task.created_at,
        "updatedAt": task.updated_at,
        "actionCount": actions,
        "failureCount": failures,
        "approvalCount": approvals,
        "denialCount": denials,
        "pendingApprovals": pending_approvals,
        "workflows": workflows,
        "lastCapability": last_capability,
    }


def recent_task_summaries(tasks: Dict[str, TaskRecord], limit: int = 50) -> List[Dict[str, Any]]:
    """Newest-first list of summaries, capped at ``limit``."""
    sorted_tasks = sorted(
        tasks.values(),
        key=lambda t: t.created_at,
        reverse=True,
    )[:max(0, limit)]
    return [task_summary(t) for t in sorted_tasks]


# ---------------------------------------------------------------------------
# Reliability counters
# ---------------------------------------------------------------------------


_COUNTER_STATUSES = ("executed", "failed", "blocked", "awaiting")


def reliability_counters(tasks: Dict[str, TaskRecord]) -> Dict[str, Any]:
    """Aggregate by-capability counters across all known tasks.

    Returned shape::

        {
          "byCapability": {
            "filesystem.read": {"executed": 12, "failed": 1,
                                "blocked": 0, "awaiting": 0},
            ...
          },
          "totals": {"tasks": 42, "actions": 87, "failures": 4,
                     "approvals": 5, "denials": 1,
                     "memoryProposed": 3, "memoryApproved": 1},
          "workflows": {"wf.open_and_read": {"completed": 2, "failed": 0}, ...},
        }
    """
    by_cap: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {s: 0 for s in _COUNTER_STATUSES}
    )
    workflows: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"completed": 0, "failed": 0}
    )
    totals = {"tasks": 0, "actions": 0, "failures": 0, "approvals": 0,
              "denials": 0, "memoryProposed": 0, "memoryApproved": 0,
              "memoryRejected": 0, "memoryExpired": 0}

    for task in tasks.values():
        totals["tasks"] += 1
        for entry in (task.trace or []):
            if not isinstance(entry, dict):
                continue
            event = entry.get("event") or ""
            if event == "action.executed":
                cap = _action_capability(entry)
                status = _action_status(entry)
                if cap:
                    if status in _COUNTER_STATUSES:
                        by_cap[cap][status] += 1
                    elif status == "awaiting_approval":
                        by_cap[cap]["awaiting"] += 1
                totals["actions"] += 1
                if status == "failed":
                    totals["failures"] += 1
            elif event == "action.blocked":
                cap = _action_capability(entry)
                if cap:
                    by_cap[cap]["blocked"] += 1
                totals["actions"] += 1
                totals["failures"] += 1
            elif event == "approval.requested":
                totals["approvals"] += 1
            elif event == "approval.denied":
                totals["denials"] += 1
            elif event == "workflow.completed":
                wf = entry.get("workflow") or {}
                pid = wf.get("patternId") or "unknown"
                workflows[pid]["completed"] += 1
            elif event == "workflow.failed":
                wf = entry.get("workflow") or {}
                pid = wf.get("patternId") or "unknown"
                workflows[pid]["failed"] += 1
            elif event == "lesson.proposed":
                totals["memoryProposed"] += 1
            elif event == "memory.approved":
                totals["memoryApproved"] += 1
            elif event == "memory.rejected":
                totals["memoryRejected"] += 1
            elif event == "memory.expired":
                totals["memoryExpired"] += 1

    return {
        "byCapability": {cap: dict(stats) for cap, stats in by_cap.items()},
        "totals": totals,
        "workflows": {pid: dict(stats) for pid, stats in workflows.items()},
    }


# ---------------------------------------------------------------------------
# Event-log health
# ---------------------------------------------------------------------------


def event_log_health(event_log: SignedEventLog) -> Dict[str, Any]:
    """Wrap ``verify_chain`` and report a non-mutating health snapshot.

    Returns ``{"ok": bool, "recordCount": int, "lengthBytes": int,
    "lastEventAt": str | None, "logPath": str}``. If verification
    raises, ``ok`` is False and the error message lands on
    ``"error"``; the log is never modified by this function.
    """
    log_path = event_log.log_path
    record_count = 0
    length_bytes = 0
    last_event_at: Optional[str] = None
    error: Optional[str] = None
    ok = True

    try:
        if log_path.exists():
            length_bytes = log_path.stat().st_size
            events = event_log.read_all()
            record_count = len(events)
            if events:
                last_event_at = events[-1].get("timestamp")
        ok = bool(event_log.verify_chain())
    except Exception as exc:  # surface, do not raise
        ok = False
        error = str(exc)

    return {
        "ok": ok,
        "recordCount": record_count,
        "lengthBytes": length_bytes,
        "lastEventAt": last_event_at,
        "logPath": str(log_path),
        "error": error,
    }
