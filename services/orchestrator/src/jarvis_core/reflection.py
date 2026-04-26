"""End-of-task reflection + sensitive-payload filter.

This module contains two responsibilities:

1. :func:`is_sensitive_payload` — the privacy filter that blocks
   long-term storage of clipboard text, OCR output, screenshot file
   contents, raw transcripts, or other user data.
2. :class:`Reflector` — runs once at the end of a task and proposes a
   small, bounded set of memories from the trace. The Reflector never
   stores sensitive data; it stores capability names, error types,
   matched-rule ids, and length-bounded sanitised excerpts of the
   user's original objective when (and only when) the objective itself
   contains an explicit preference phrase.

The Reflector is deliberately conservative. It is **not** an LLM. It
fires on a tight, explicit set of trace patterns:

* a tool failed → ``tool`` note (capability + error_type, no body)
* a workflow completed → ``operational`` note (pattern_id + step count)
* a planner emitted ``clarification_needed`` → ``lesson`` note
  (matched_rule + sanitised excerpt of the objective)
* the objective contained an explicit-preference phrase
  ("I prefer …", "always …", "never …", "from now on …") → ``profile`` note
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import MemoryItem, TaskRecord


# ---------------------------------------------------------------------------
# Sensitive-payload filter
# ---------------------------------------------------------------------------

# Keys that, when present in ``details`` with a non-trivial string value,
# almost certainly carry user content we MUST NOT promote into memory.
_SENSITIVE_KEYS = {
    "text",          # clipboard text, OCR text, raw transcripts
    "transcript",    # voice transcript
    "excerpt",       # browser-context excerpts
    "raw_text",
    "raw_audio",
    "audio",
    "audio_base64",
    "ocr_text",
    "clipboard",
    "screenshot",    # screenshot file contents
    "screenshot_bytes",
    "png_bytes",
    "content",       # filesystem.write content body
}

# Verbs in the *summary* string that indicate the proposer is leaking
# user data even if no canonical key was used.
_SENSITIVE_PHRASE_RE = re.compile(
    r"(?:"
    r"clipboard\s+(?:was|contain(?:s|ed)?|read[s]?|contents?|excerpt|text|had|holds?)"
    r"|ocr\s+(?:result|text|output|extracted)"
    r"|extracted\s+text"
    r"|screenshot\s+(?:bytes|content|text|data)"
    r"|transcript\s+(?:was|contain(?:s|ed)?|read[s]?|text)"
    r"|user\s+typed\s+the\s+text"
    r")",
    re.IGNORECASE,
)

# When a verb in the original objective points at user-content extraction,
# we won't auto-propose a ``profile`` memory from it. Otherwise "I prefer
# to read /etc/passwd" would land in memory as a preference.
_SENSITIVE_OBJECTIVE_VERBS_RE = re.compile(
    r"\b(?:clipboard|ocr|screenshot|transcript|extract|read\s+text|"
    r"voice|microphone|password|secret|token|credential)\b",
    re.IGNORECASE,
)


def is_sensitive_payload(item: MemoryItem) -> Optional[str]:
    """Return a refusal reason if the memory carries user content, else None.

    The check inspects the summary, evidence, and details. We are
    deliberately strict: a single sensitive key with a non-empty string
    value is enough to refuse. This errs on the side of *not* storing
    rather than storing accidentally.
    """
    summary = (item.summary or "").strip()
    if _SENSITIVE_PHRASE_RE.search(summary):
        return "summary appears to contain user content (clipboard/ocr/transcript/screenshot)."

    # Long, free-form summaries are suspicious for the same reason. The
    # bounded length cap keeps the audit log human-readable too.
    if len(summary) > 800:
        return "summary exceeds 800 characters; refusing to store free-form user text."

    evidence = item.evidence or []
    if any(_SENSITIVE_PHRASE_RE.search(str(piece)) for piece in evidence):
        return "evidence list mentions user content (clipboard/ocr/transcript/screenshot)."

    details = item.details or {}
    for key, value in details.items():
        if str(key).lower() in _SENSITIVE_KEYS and _looks_like_user_text(value):
            return f"details[{key!r}] carries user content; refusing to store verbatim."
    return None


def _looks_like_user_text(value: Any) -> bool:
    """A value is considered user-text iff it is a non-trivial string.

    ``True`` values, integers, booleans, dicts that only carry metadata,
    and empty/None values are NOT user content. The filter is designed
    to flag actual character payloads, not flags.
    """
    if isinstance(value, str):
        return len(value.strip()) > 0
    if isinstance(value, (bytes, bytearray)):
        return len(value) > 0
    return False


# ---------------------------------------------------------------------------
# Reflector
# ---------------------------------------------------------------------------

# Explicit preference phrases. Conservative on purpose — we ONLY mine the
# objective itself, never any tool output. If the user says "always use
# https when no scheme is given", that becomes a profile memory. If a
# subagent produces a similar string, we ignore it.
_PREFERENCE_PATTERNS = [
    re.compile(r"^\s*i\s+prefer\s+(.+?)\s*\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*i'?d\s+prefer\s+(.+?)\s*\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*always\s+(.+?)\s*\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*never\s+(.+?)\s*\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*from\s+now\s+on,?\s+(.+?)\s*\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*please\s+always\s+(.+?)\s*\.?\s*$", re.IGNORECASE),
]

_MAX_OBJECTIVE_EXCERPT = 200


def _safe_excerpt(text: str, *, limit: int = _MAX_OBJECTIVE_EXCERPT) -> str:
    """Return a sanitised, length-bounded excerpt of an objective.

    Used for lesson summaries that need to reference the user's wording
    without storing the entire phrase. We strip newlines so the row
    stays one-line readable in the HUD.
    """
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


class Reflector:
    """Runs once at the end of a task; proposes a bounded memory set.

    Stateless across tasks. The ``reflect_on_task`` method dedupes
    within a single call to prevent the same lesson from being filed
    twice when the same trace pattern occurs more than once.

    The Reflector never invokes the gateway or the policy engine. It
    only proposes memories — every proposal is still a candidate that
    the user must approve before it influences anything.
    """

    def __init__(self, memory_store: Any) -> None:
        self._memory = memory_store

    # ------------------------------------------------------------------
    def reflect_on_task(self, task: TaskRecord) -> List[Dict[str, Any]]:
        """Walk the task trace + context and propose any matching memories.

        Returns the list of proposed memories (already persisted as
        candidates), so the caller can append a ``reflection.proposed``
        trace event.
        """
        proposed: List[Dict[str, Any]] = []
        seen: Set[Tuple[str, str]] = set()

        # 1. Profile memory from explicit-preference objective.
        pref = self._extract_preference(task.objective)
        if pref is not None:
            key = ("profile", pref.lower())
            if key not in seen:
                seen.add(key)
                item = self._safe_propose(MemoryItem(
                    kind="profile",
                    summary=f"User preference: {pref}",
                    details={"objective_excerpt": _safe_excerpt(task.objective),
                             "task_id": task.task_id},
                    evidence=[f"task:{task.task_id}", "objective"],
                    trust_score=0.6,
                ))
                if item is not None:
                    proposed.append(item)

        # 2/3. Walk the trace for tool failures, planner clarifications,
        #      and workflow completions.
        for entry in (task.trace or []):
            event = entry.get("event") or ""

            if event == "action.executed":
                # action.executed events also carry failed/blocked results
                # — the supervisor uses one event with a status field.
                tool_record = self._reflect_on_action_result(entry, task)
                if tool_record:
                    key = ("tool", tool_record["dedup_key"])
                    if key in seen:
                        continue
                    seen.add(key)
                    item = self._safe_propose(MemoryItem(
                        kind="tool",
                        summary=tool_record["summary"],
                        details=tool_record["details"],
                        evidence=tool_record["evidence"],
                        trust_score=0.55,
                    ))
                    if item is not None:
                        proposed.append(item)

            elif event == "action.blocked":
                blocked = self._reflect_on_blocked(entry, task)
                if blocked:
                    key = ("tool", blocked["dedup_key"])
                    if key in seen:
                        continue
                    seen.add(key)
                    item = self._safe_propose(MemoryItem(
                        kind="tool",
                        summary=blocked["summary"],
                        details=blocked["details"],
                        evidence=blocked["evidence"],
                        trust_score=0.5,
                    ))
                    if item is not None:
                        proposed.append(item)

            elif event == "plan.evaluated":
                lesson = self._reflect_on_plan(entry, task)
                if lesson:
                    key = ("lesson", lesson["dedup_key"])
                    if key in seen:
                        continue
                    seen.add(key)
                    item = self._safe_propose(MemoryItem(
                        kind="lesson",
                        summary=lesson["summary"],
                        details=lesson["details"],
                        evidence=lesson["evidence"],
                        trust_score=0.55,
                    ))
                    if item is not None:
                        proposed.append(item)

            elif event == "workflow.completed":
                op = self._reflect_on_workflow(entry, task)
                if op:
                    key = ("operational", op["dedup_key"])
                    if key in seen:
                        continue
                    seen.add(key)
                    item = self._safe_propose(MemoryItem(
                        kind="operational",
                        summary=op["summary"],
                        details=op["details"],
                        evidence=op["evidence"],
                        trust_score=0.6,
                    ))
                    if item is not None:
                        proposed.append(item)

        return proposed

    # ------------------------------------------------------------------
    # Per-event reflectors
    # ------------------------------------------------------------------

    def _reflect_on_action_result(self, entry: Dict[str, Any], task: TaskRecord) -> Optional[Dict[str, Any]]:
        result = (entry.get("result") or {}) if isinstance(entry, dict) else {}
        if (result.get("status") or "") != "failed":
            return None
        proposal = result.get("proposal") or {}
        capability = proposal.get("capability") or "unknown"
        output = result.get("output") or {}
        error_type = output.get("error_type") or output.get("error") or "unspecified"
        # Cap the error_type/key length defensively — error_type strings
        # are usually short class names; if a provider stuffed user text
        # into it, the cap protects us.
        error_type = str(error_type)[:120]
        summary = f"{capability} failed with error_type={error_type}."
        details = {
            "capability": capability,
            "error_type": error_type,
            "task_id": task.task_id,
        }
        return {
            "summary": summary,
            "details": details,
            "evidence": [f"task:{task.task_id}", f"capability:{capability}"],
            "dedup_key": f"{capability}|{error_type}",
        }

    def _reflect_on_blocked(self, entry: Dict[str, Any], task: TaskRecord) -> Optional[Dict[str, Any]]:
        result = (entry.get("result") or {}) if isinstance(entry, dict) else {}
        proposal = result.get("proposal") or {}
        capability = proposal.get("capability") or "unknown"
        decision = result.get("decision") or {}
        reason = decision.get("reason") or "blocked by policy"
        summary = f"{capability} was blocked by policy ({_safe_excerpt(reason, limit=120)})."
        return {
            "summary": summary,
            "details": {"capability": capability,
                         "reason": _safe_excerpt(reason, limit=120),
                         "task_id": task.task_id},
            "evidence": [f"task:{task.task_id}", "policy.blocked"],
            "dedup_key": f"{capability}|blocked",
        }

    def _reflect_on_plan(self, entry: Dict[str, Any], task: TaskRecord) -> Optional[Dict[str, Any]]:
        plan = entry.get("plan") or {}
        status = plan.get("status")
        if status != "clarification_needed":
            return None
        rule = plan.get("matchedRule") or "unknown_rule"
        ambiguity = plan.get("ambiguity") or "ambiguous request"
        excerpt = _safe_excerpt(task.objective)
        summary = (
            f"Planner clarification ({rule}): {_safe_excerpt(ambiguity, limit=160)}"
        )
        return {
            "summary": summary,
            "details": {
                "matched_rule": rule,
                "ambiguity": _safe_excerpt(ambiguity, limit=240),
                "objective_excerpt": excerpt,
                "task_id": task.task_id,
            },
            "evidence": [f"task:{task.task_id}", f"rule:{rule}"],
            "dedup_key": f"clarification|{rule}",
        }

    def _reflect_on_workflow(self, entry: Dict[str, Any], task: TaskRecord) -> Optional[Dict[str, Any]]:
        wf = entry.get("workflow") or {}
        pattern_id = wf.get("patternId") or "unknown"
        steps = wf.get("steps") or []
        capabilities = [s.get("capability") for s in steps if s.get("capability")]
        summary = (
            f"Workflow {pattern_id} completed cleanly in {len(steps)} step"
            + ("s" if len(steps) != 1 else "")
            + "."
        )
        return {
            "summary": summary,
            "details": {
                "pattern_id": pattern_id,
                "step_capabilities": capabilities[:10],
                "task_id": task.task_id,
            },
            "evidence": [f"task:{task.task_id}", f"workflow:{pattern_id}"],
            "dedup_key": f"workflow|{pattern_id}",
        }

    # ------------------------------------------------------------------

    def _extract_preference(self, objective: str) -> Optional[str]:
        text = (objective or "").strip()
        if not text:
            return None
        # Skip if the objective itself is about extracting user content.
        if _SENSITIVE_OBJECTIVE_VERBS_RE.search(text):
            return None
        for pat in _PREFERENCE_PATTERNS:
            m = pat.match(text)
            if m:
                phrase = m.group(1).strip()
                if not phrase:
                    return None
                # Re-check the captured phrase against the same sensitive
                # filter so "always store the clipboard contents" can't
                # become a profile memory.
                if _SENSITIVE_OBJECTIVE_VERBS_RE.search(phrase):
                    return None
                return _safe_excerpt(phrase, limit=160)
        return None

    def _safe_propose(self, item: MemoryItem) -> Optional[Dict[str, Any]]:
        """Persist a proposal; swallow MemoryRejectedError and skip silently.

        Any rejection is itself a sign that the proposer would have
        leaked sensitive data — we log it on the supervisor side via
        the trace, but the memory store stays clean.
        """
        try:
            stored = self._memory.propose(item)
        except Exception:
            return None
        return stored.to_dict()


# ---------------------------------------------------------------------------
# Memory hint provider — read-only view used by the planner
# ---------------------------------------------------------------------------


class ApprovedMemoryHints:
    """Read-only view of approved memories, used by the planner.

    Memory hints are *advisory only*. They never change the chosen
    capability, parameters, or confidence. They produce
    ``PlanResult.memoryHints`` so the HUD can surface that approved
    memory was relevant to the decision.
    """

    def __init__(self, memory_store: Any) -> None:
        self._memory = memory_store

    def hints_for(self, *, capability: Optional[str] = None,
                  matched_rule: Optional[str] = None,
                  kinds: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        approved = self._memory.list(status="approved")
        if not approved:
            return []
        if kinds is None:
            kinds = ["profile", "lesson", "tool"]
        out: List[Dict[str, Any]] = []
        for row in approved:
            if row.get("kind") not in kinds:
                continue
            details = row.get("details") or {}
            row_rule = details.get("matched_rule")
            row_cap = details.get("capability")
            # A hint is relevant when it touches the same matched_rule
            # OR the same capability. Profile memories don't carry a
            # rule/capability link, so they are always surfaced.
            relevant = (
                row.get("kind") == "profile"
                or (matched_rule is not None and row_rule == matched_rule)
                or (capability is not None and row_cap == capability)
            )
            if not relevant:
                continue
            out.append({
                "memoryId": row.get("memory_id"),
                "kind": row.get("kind"),
                "summary": row.get("summary"),
                "trustScore": row.get("trust_score"),
            })
        return out
