"""Deterministic command interpreter for typed / spoken task text.

The planner is **intentionally narrow**. It only maps a small, explicit
set of natural-language patterns onto the structured capabilities that
already exist in this repo. Anything else it returns as
``clarification_needed`` or ``unsupported`` — it never guesses.

Contract
--------
``DeterministicPlanner.plan(text)`` always returns a :class:`PlanResult`.
The result is inspectable (``to_dict``) so the HUD and traces can show
exactly which rule matched, what parameters were extracted, and why.

This planner does **not** execute anything. It does not call the
ActionGateway or the PolicyEngine — it only produces an
``ActionProposal`` draft that the caller then feeds into the existing
``SupervisorRuntime.propose_action`` path. Tier gating, approval
queueing, blocked-pattern rejection, and the audit log all continue
to come from the gateway; the planner's output is just a structured
suggestion.

Supported v1 intents
--------------------
Only these specific shapes are recognised:

    open a URL          →  browser.navigate
    read a URL          →  browser.read_page
    read a file         →  filesystem.read
    list a directory    →  filesystem.list
    write to sandbox    →  filesystem.write
    launch an app       →  app.launch

The app allowlist matches the existing ApplicationCapability
allowlist (notepad, calc, calculator, explorer, mspaint). Write
targets must resolve inside ``runtime/sandbox/``. Anything else is
returned unmapped rather than executed, even if the request looks
similar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# Mirror of the runtime app allowlist. Kept in sync with
# capabilities/applications.py::_DEFAULT_ALLOWLIST. Duplicating the
# set (rather than importing it) keeps the planner free from a runtime
# dependency on resolved executable paths — the planner only decides
# *whether* the name is plausibly allowed; the adapter still enforces.
APP_ALLOWLIST = {"notepad", "calc", "calculator", "explorer", "mspaint"}

_APP_ALIASES: Dict[str, str] = {
    "notepad":      "notepad",
    "calculator":   "calculator",
    "calc":         "calc",
    "explorer":     "explorer",
    "file explorer": "explorer",
    "files":        "explorer",
    "paint":        "mspaint",
    "mspaint":      "mspaint",
    "ms paint":     "mspaint",
}

_OPEN_VERBS = r"(?:open|launch|start|run)"
_NAV_VERBS = r"(?:open|go\s+to|navigate\s+to|visit|browse\s+to)"
_READ_VERBS = r"(?:read|fetch|show)"
_LIST_VERBS = r"(?:list|show|display|ls|dir)"
_WRITE_VERBS = r"(?:write|save|put)"
_SUMMARIZE_VERBS = r"(?:summari[sz]e|summary\s+of|tl;dr\s+of|tldr\s+of)"

# Phrases that refer to the current browser context instead of a URL.
_CURRENT_PAGE_RE = re.compile(
    r"^\s*(?:this|that|the\s+(?:current\s+)?)\s*(?:page|site|tab|url)\s*$",
    re.IGNORECASE,
)
_WHAT_PAGE_RE = re.compile(
    r"^\s*(?:what|which)\s+(?:page|site|url|tab)\s+(?:am\s+i\s+on|is\s+(?:this|open|current|active)|did\s+i\s+(?:just\s+)?(?:open|visit))\s*\??\s*$",
    re.IGNORECASE,
)

# A "URL-ish" token. We DO NOT accept bare words like "notepad" here.
# Either the string has a scheme (https?://...) or it has at least one
# dot surrounded by domain-valid characters and no spaces.
_URL_RE = re.compile(
    r"^(?:https?://[^\s]+"
    r"|(?:[a-z0-9][a-z0-9-]*\.)+[a-z]{2}[a-z0-9.-]*(?:/[^\s]*)?)$",
    re.IGNORECASE,
)

# A "path-ish" token. Either contains a path separator (optionally with a
# leading Windows drive letter), or looks like a filename with an extension.
# Rejects bare words.
_PATH_RE = re.compile(
    r"^(?:[a-z]:[/\\][^\s]*"
    r"|[a-z0-9._/\\-]+[/\\][a-z0-9._/\\-]*"
    r"|[a-z0-9._-]+\.[a-z0-9]{1,8})$",
    re.IGNORECASE,
)

# Vague deictic references that should trigger clarification, never a guess.
_VAGUE_TARGETS = {
    "this", "that", "it", "this page", "that page", "the page",
    "this file", "that file", "the file", "here",
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

MAPPED = "mapped"
CLARIFICATION_NEEDED = "clarification_needed"
UNSUPPORTED = "unsupported"


@dataclass(slots=True)
class PlanResult:
    """Output of the deterministic planner. Always inspectable."""

    status: str                             # MAPPED | CLARIFICATION_NEEDED | UNSUPPORTED
    original_text: str
    capability: Optional[str] = None         # set when status == MAPPED
    parameters: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0                  # 0.0 – 1.0
    rationale: str = ""                      # why the mapping was made
    ambiguity: Optional[str] = None          # why we declined to map
    matched_rule: Optional[str] = None       # stable id of the rule that fired

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "originalText": self.original_text,
            "capability": self.capability,
            "parameters": dict(self.parameters),
            "confidence": self.confidence,
            "rationale": self.rationale,
            "ambiguity": self.ambiguity,
            "matchedRule": self.matched_rule,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase + collapse whitespace + strip common trailing punctuation."""
    t = (text or "").strip()
    # Keep case for URLs / paths later — we pattern-match on lowercase copies
    # but preserve the original string for parameter extraction.
    t = re.sub(r"\s+", " ", t)
    t = t.rstrip(".?!")
    return t


def _looks_like_url(token: str) -> bool:
    return bool(_URL_RE.match(token.strip()))


def _looks_like_path(token: str) -> bool:
    if not token or _looks_like_url(token):
        return False
    return bool(_PATH_RE.match(token.strip()))


def _normalize_url(raw: str) -> str:
    """Add scheme if missing. Never rewrite an existing scheme."""
    raw = raw.strip().rstrip(".,;")
    if re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.IGNORECASE):
        return raw
    return "https://" + raw


def _app_alias(token: str) -> Optional[str]:
    """Return the canonical allowlisted app name for a phrase, else None."""
    key = token.strip().lower()
    if key in _APP_ALIASES:
        return _APP_ALIASES[key]
    if key in APP_ALLOWLIST:
        return key
    return None


def _strip_leading_article(s: str) -> str:
    return re.sub(r"^(?:the|a|an)\s+", "", s.strip(), flags=re.IGNORECASE)


def _tail_after(text: str, verb_pattern: str) -> Optional[str]:
    """Return whatever follows the first match of `verb_pattern`, or None."""
    m = re.match(rf"^\s*{verb_pattern}\s+(.+?)\s*$", text, re.IGNORECASE)
    if not m:
        return None
    return m.group(1).strip()


def _is_sandbox_path(path: str) -> bool:
    """True if the path clearly targets ``runtime/sandbox/`` (writable root).

    Accepts relative (``runtime/sandbox/foo``) and absolute
    (``C:/…/runtime/sandbox/foo``) forms. We do NOT touch the filesystem
    here — the gateway enforces the real scope check. This just filters
    out obvious non-sandbox writes so the planner doesn't auto-propose
    something guaranteed to be refused.
    """
    p = path.replace("\\", "/").lstrip("./")
    if p.startswith("runtime/sandbox/") or p.startswith("sandbox/"):
        return True
    return "/runtime/sandbox/" in p or "/sandbox/" in p


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

class DeterministicPlanner:
    """Rule-based, LLM-free interpreter.

    Rules are checked in priority order. The first rule that produces
    a :class:`PlanResult` wins. A rule may return ``None`` to delegate
    to the next rule, a MAPPED result when it is confident, or a
    CLARIFICATION_NEEDED result when it recognised the shape but needs
    a missing parameter.
    """

    def plan(self, text: str, *, has_browser_context: bool = False) -> PlanResult:
        normalized = _normalize(text)
        if not normalized:
            return PlanResult(
                status=UNSUPPORTED,
                original_text=text or "",
                ambiguity="Empty request.",
                matched_rule="empty",
            )

        lower = normalized.lower()

        self._has_browser_context = bool(has_browser_context)
        for rule in (
            self._rule_current_page_query,
            self._rule_summarize,
            self._rule_clipboard_read,
            self._rule_clipboard_write,
            self._rule_notify,
            self._rule_foreground_window,
            self._rule_screenshot,
            self._rule_focus_app,
            self._rule_write_file,
            self._rule_list_directory,
            self._rule_read_page_or_file,
            self._rule_open_and_read,
            self._rule_open_app_or_url,
            self._rule_navigate_verbs,
        ):
            result = rule(normalized, lower)
            if result is not None:
                return result

        return PlanResult(
            status=UNSUPPORTED,
            original_text=text,
            ambiguity=(
                "No deterministic rule matched this request. Supported v1 intents: "
                "open a URL, read a page, read a file, list a directory, write to "
                "the sandbox, launch an allowlisted app."
            ),
            matched_rule="fallthrough",
        )

    # ------------------------------------------------------------------
    # Individual rules
    # ------------------------------------------------------------------

    def _rule_current_page_query(self, text: str, lower: str) -> Optional[PlanResult]:
        """'what page am I on?' / 'which page is open?' → browser.current_page."""
        if not _WHAT_PAGE_RE.match(text):
            return None
        if not self._has_browser_context:
            return PlanResult(
                status=CLARIFICATION_NEEDED,
                original_text=text,
                ambiguity=(
                    "No current page context. The assistant has not read a page "
                    "yet and no snapshot has been pushed. Ask me to read a URL "
                    "first, e.g. 'read https://example.com'."
                ),
                matched_rule="current_page.no_context",
            )
        return PlanResult(
            status=MAPPED,
            original_text=text,
            capability="browser.current_page",
            parameters={},
            confidence=0.95,
            rationale="Matched 'what page am I on?' → browser.current_page (from context).",
            matched_rule="current_page.query",
        )

    def _rule_summarize(self, text: str, lower: str) -> Optional[PlanResult]:
        """'summarize this page' / 'summarize <url>' → browser.summarize."""
        m = re.match(
            rf"^\s*{_SUMMARIZE_VERBS}\s+(?:the\s+)?(.+?)\s*$",
            text, re.IGNORECASE,
        )
        if not m:
            return None
        target_raw = _strip_leading_article(m.group(1).strip().rstrip(".,;?"))
        target_lower = target_raw.lower()

        # "summarize this page" / "summarize current page" / "summarize it"
        if (target_lower in _VAGUE_TARGETS
                or _CURRENT_PAGE_RE.match(target_raw)
                or target_lower in {"current page", "the current page", "page", "site"}):
            if not self._has_browser_context:
                return PlanResult(
                    status=CLARIFICATION_NEEDED,
                    original_text=text,
                    ambiguity=(
                        "No current page context to summarise. Ask me to read a "
                        "URL first (e.g. 'read https://example.com'), then try again."
                    ),
                    matched_rule="summarize.no_context",
                )
            return PlanResult(
                status=MAPPED,
                original_text=text,
                capability="browser.summarize",
                parameters={"use_context": True},
                confidence=0.9,
                rationale="Matched 'summarize <current page>' → browser.summarize (context).",
                matched_rule="summarize.context",
            )

        if _looks_like_url(target_raw):
            url = _normalize_url(target_raw)
            return PlanResult(
                status=MAPPED,
                original_text=text,
                capability="browser.summarize",
                parameters={"url": url},
                confidence=0.9,
                rationale=f"Matched 'summarize <URL>' → browser.summarize ({url}).",
                matched_rule="summarize.url",
            )

        return PlanResult(
            status=CLARIFICATION_NEEDED,
            original_text=text,
            ambiguity=(
                f"'summarize {target_raw}' — {target_raw!r} is neither a URL nor a "
                "reference to the current page. Provide a URL, or say 'summarize "
                "this page' after reading one."
            ),
            matched_rule="summarize.ambiguous",
        )

    def _rule_open_and_read(self, text: str, lower: str) -> Optional[PlanResult]:
        """'open <URL> and read it' / 'open <URL> and summarize it' → browser.read_page / summarize.

        Maps to a single structured fetch + extract. Does NOT also open
        the URL in the OS browser — we fetch the content directly so the
        assistant can read it. If the user wants a visual open too, they
        can say 'open <URL>' separately.
        """
        m = re.match(
            rf"^\s*{_NAV_VERBS}\s+(\S+)\s+and\s+(read|summari[sz]e)(?:\s+it)?\s*$",
            text, re.IGNORECASE,
        )
        if not m:
            return None
        raw_url = m.group(1).strip().rstrip(".,;")
        verb = m.group(2).lower()
        if not _looks_like_url(raw_url):
            return PlanResult(
                status=CLARIFICATION_NEEDED,
                original_text=text,
                ambiguity=f"'{raw_url}' is not a URL. Provide a full URL or a domain with a TLD.",
                matched_rule="open_and_read.bad_url",
            )
        url = _normalize_url(raw_url)
        capability = "browser.summarize" if verb.startswith("summar") else "browser.read_page"
        return PlanResult(
            status=MAPPED,
            original_text=text,
            capability=capability,
            parameters={"url": url},
            confidence=0.88,
            rationale=(
                f"Matched 'open <URL> and {verb} it' → {capability} ({url}). "
                "Fetches content directly; does not also open the OS browser."
            ),
            matched_rule=f"open_and_{verb.rstrip('e')}",
        )

    def _rule_write_file(self, text: str, lower: str) -> Optional[PlanResult]:
        # "write <content> to <path>" / "save <content> to <path>"
        m = re.match(
            rf"^\s*{_WRITE_VERBS}\s+(.+?)\s+(?:to|into)\s+(\S+)\s*$",
            text, re.IGNORECASE,
        )
        if not m:
            return None
        content, path = m.group(1).strip(), m.group(2).strip()
        # Strip surrounding quotes on content — common for "write 'hi' to ..."
        if len(content) >= 2 and content[0] == content[-1] and content[0] in ("'", '"'):
            content = content[1:-1]

        if not _looks_like_path(path):
            return PlanResult(
                status=CLARIFICATION_NEEDED,
                original_text=text,
                ambiguity=f"Write target {path!r} does not look like a file path.",
                matched_rule="write.bad_path",
            )
        if not _is_sandbox_path(path):
            return PlanResult(
                status=CLARIFICATION_NEEDED,
                original_text=text,
                ambiguity=(
                    f"Refusing to auto-plan a write to {path!r}: target must be under "
                    "'runtime/sandbox/'. Rephrase with a sandbox path (e.g. "
                    "'write hello to runtime/sandbox/hello.txt')."
                ),
                matched_rule="write.outside_sandbox",
            )
        return PlanResult(
            status=MAPPED,
            original_text=text,
            capability="filesystem.write",
            parameters={"path": path, "content": content},
            confidence=0.92,
            rationale=f"Matched 'write <content> to <sandbox path>' → filesystem.write ({path}).",
            matched_rule="write.to_sandbox",
        )

    def _rule_list_directory(self, text: str, lower: str) -> Optional[PlanResult]:
        # "list files in <dir>" / "show files in <dir>" / "list <dir>" / "ls <dir>"
        patterns: List[Tuple[str, str]] = [
            (rf"^\s*{_LIST_VERBS}\s+(?:files|contents|directory|folder)\s+(?:in|of|at|under)\s+(\S+)\s*$",
             "list.files_in"),
            (rf"^\s*(?:list|ls|dir)\s+(\S+)\s*$", "list.short"),
        ]
        for pat, rule_id in patterns:
            m = re.match(pat, text, re.IGNORECASE)
            if not m:
                continue
            target = m.group(1).strip()
            if _looks_like_url(target):
                return PlanResult(
                    status=CLARIFICATION_NEEDED,
                    original_text=text,
                    ambiguity=f"Cannot list a URL ({target}). Provide a directory path.",
                    matched_rule=f"{rule_id}.url_rejected",
                )
            if not _looks_like_path(target) and "/" not in target and "\\" not in target:
                # Short directory names like "configs" are fine — they resolve
                # relative to workspace_root at the adapter. Only reject if
                # the token looks like random nonsense. A single bare word is
                # allowed.
                if not re.match(r"^[a-z0-9._-]+$", target, re.IGNORECASE):
                    return PlanResult(
                        status=CLARIFICATION_NEEDED,
                        original_text=text,
                        ambiguity=f"Directory target {target!r} is not a valid path.",
                        matched_rule=f"{rule_id}.bad_path",
                    )
            return PlanResult(
                status=MAPPED,
                original_text=text,
                capability="filesystem.list",
                parameters={"path": target},
                confidence=0.9,
                rationale=f"Matched '{rule_id}' → filesystem.list ({target}).",
                matched_rule=rule_id,
            )
        return None

    def _rule_read_page_or_file(self, text: str, lower: str) -> Optional[PlanResult]:
        # "read <something>" / "read the page at <url>" / "fetch <url>"
        # Also: "read this page" → clarification.
        m = re.match(
            rf"^\s*{_READ_VERBS}\s+(?:the\s+)?(?:page\s+(?:at|from)\s+|file\s+)?(.+?)\s*$",
            text, re.IGNORECASE,
        )
        if not m:
            return None
        target = _strip_leading_article(m.group(1).strip())

        target_low = target.lower()
        is_page_deictic = (
            target_low in {"this page", "that page", "the page", "current page",
                           "the current page"}
            or bool(_CURRENT_PAGE_RE.match(target))
        )
        if is_page_deictic:
            if not self._has_browser_context:
                return PlanResult(
                    status=CLARIFICATION_NEEDED,
                    original_text=text,
                    ambiguity=(
                        "No current page context. Ask me to read a URL first "
                        "(e.g. 'read https://example.com') or 'summarize https://…'."
                    ),
                    matched_rule="read.page_no_context",
                )
            return PlanResult(
                status=MAPPED,
                original_text=text,
                capability="browser.current_page",
                parameters={},
                confidence=0.9,
                rationale="Matched 'read this page' → browser.current_page (from context).",
                matched_rule="read.current_page",
            )
        if target_low in _VAGUE_TARGETS:
            return PlanResult(
                status=CLARIFICATION_NEEDED,
                original_text=text,
                ambiguity=(
                    f"Deictic target {target!r} — I won't guess what 'this' refers to. "
                    "Provide a URL or a file path explicitly."
                ),
                matched_rule="read.deictic",
            )

        if _looks_like_url(target):
            url = _normalize_url(target)
            return PlanResult(
                status=MAPPED,
                original_text=text,
                capability="browser.read_page",
                parameters={"url": url},
                confidence=0.9,
                rationale=f"Matched 'read <URL>' → browser.read_page ({url}).",
                matched_rule="read.url",
            )
        if _looks_like_path(target):
            return PlanResult(
                status=MAPPED,
                original_text=text,
                capability="filesystem.read",
                parameters={"path": target},
                confidence=0.88,
                rationale=f"Matched 'read <path>' → filesystem.read ({target}).",
                matched_rule="read.path",
            )

        # Lone word like "read notepad" — doesn't pattern-match URL or path.
        return PlanResult(
            status=CLARIFICATION_NEEDED,
            original_text=text,
            ambiguity=(
                f"'read {target}' is ambiguous: {target!r} is neither a URL nor a "
                "file path. If you meant a web page, include the scheme or a TLD "
                "(e.g. 'read https://example.com'). If you meant a file, include "
                "an extension or directory separator."
            ),
            matched_rule="read.ambiguous",
        )

    def _rule_open_app_or_url(self, text: str, lower: str) -> Optional[PlanResult]:
        # "open <X>" / "launch <X>" / "start <X>" / "run <X>"
        m = re.match(rf"^\s*{_OPEN_VERBS}\s+(?:the\s+)?(.+?)\s*$", text, re.IGNORECASE)
        if not m:
            return None
        target_raw = m.group(1).strip().rstrip(".,;")
        target_lower = target_raw.lower()

        # App match wins over URL (disambiguates "open notepad" vs "open example.com").
        app = _app_alias(target_lower)
        if app is not None:
            return PlanResult(
                status=MAPPED,
                original_text=text,
                capability="app.launch",
                parameters={"name": app},
                confidence=0.95,
                rationale=f"Matched allowlisted app '{app}' → app.launch.",
                matched_rule="open.app",
            )

        if _looks_like_url(target_raw):
            url = _normalize_url(target_raw)
            return PlanResult(
                status=MAPPED,
                original_text=text,
                capability="browser.navigate",
                parameters={"url": url},
                confidence=0.9,
                rationale=f"Matched 'open <URL>' → browser.navigate ({url}).",
                matched_rule="open.url",
            )

        # Vague deictic.
        if target_lower in _VAGUE_TARGETS:
            return PlanResult(
                status=CLARIFICATION_NEEDED,
                original_text=text,
                ambiguity=f"'open {target_raw}' — no explicit target. Name the app or URL.",
                matched_rule="open.deictic",
            )

        # Bare word that is neither app nor URL. We do NOT guess. The
        # request looked like an "open …" command but the target is not
        # on the allowlist and isn't a URL.
        return PlanResult(
            status=CLARIFICATION_NEEDED,
            original_text=text,
            ambiguity=(
                f"'open {target_raw}' — {target_raw!r} is not an allowlisted app "
                f"({sorted(APP_ALLOWLIST)}) and does not look like a URL. "
                "Include a scheme (https://…) or use an allowlisted app name."
            ),
            matched_rule="open.unknown_target",
        )

    # ------------------------------------------------------------------
    # Desktop rules
    # ------------------------------------------------------------------

    def _rule_clipboard_read(self, text: str, lower: str) -> Optional[PlanResult]:
        # "what is in my clipboard", "read my clipboard", "show clipboard",
        # "what's in the clipboard", "paste my clipboard"
        patterns = [
            r"^\s*what(?:'s| is)\s+(?:in\s+)?(?:my|the)\s+clipboard\s*\??\s*$",
            r"^\s*(?:read|show|display|paste|get)\s+(?:my|the)\s+clipboard\s*$",
            r"^\s*(?:read|show)\s+clipboard\s*$",
            r"^\s*clipboard\s*\??\s*$",
        ]
        for pat in patterns:
            if re.match(pat, text, re.IGNORECASE):
                return PlanResult(
                    status=MAPPED,
                    original_text=text,
                    capability="desktop.clipboard_read",
                    parameters={},
                    confidence=0.95,
                    rationale="Matched clipboard-query phrasing → desktop.clipboard_read.",
                    matched_rule="desktop.clipboard_read",
                )
        return None

    def _rule_clipboard_write(self, text: str, lower: str) -> Optional[PlanResult]:
        # "copy <text> to clipboard" / "copy <text> to the clipboard"
        # "put <text> on clipboard" / "set clipboard to <text>"
        m = re.match(
            r"^\s*(?:copy|put)\s+(.+?)\s+(?:to|on|into)\s+(?:the\s+|my\s+)?clipboard\s*$",
            text, re.IGNORECASE,
        )
        if not m:
            m = re.match(
                r"^\s*set\s+(?:the\s+|my\s+)?clipboard\s+to\s+(.+?)\s*$",
                text, re.IGNORECASE,
            )
        if not m:
            return None
        content = m.group(1).strip()
        if len(content) >= 2 and content[0] == content[-1] and content[0] in ("'", '"'):
            content = content[1:-1]
        if not content:
            return PlanResult(
                status=CLARIFICATION_NEEDED,
                original_text=text,
                ambiguity="Clipboard content is empty after stripping quotes.",
                matched_rule="desktop.clipboard_write.empty",
            )
        return PlanResult(
            status=MAPPED,
            original_text=text,
            capability="desktop.clipboard_write",
            parameters={"text": content},
            confidence=0.9,
            rationale=f"Matched 'copy <text> to clipboard' → desktop.clipboard_write ({len(content)} chars).",
            matched_rule="desktop.clipboard_write",
        )

    def _rule_notify(self, text: str, lower: str) -> Optional[PlanResult]:
        # "notify me <message>" / "send me a notification saying <message>"
        # "send a notification saying <message>" / "show notification <message>"
        patterns = [
            (r"^\s*(?:send|give|show)\s+(?:me\s+)?(?:a\s+)?notification\s+"
             r"(?:saying|that\s+says|with)\s+(.+?)\s*$", "notify.say"),
            (r"^\s*notify\s+(?:me\s+)?(?:saying\s+|that\s+|with\s+)?(.+?)\s*$", "notify.me"),
            (r"^\s*show\s+(?:a\s+)?notification\s+(.+?)\s*$", "notify.show"),
        ]
        for pat, rule_id in patterns:
            m = re.match(pat, text, re.IGNORECASE)
            if not m:
                continue
            message = m.group(1).strip()
            if len(message) >= 2 and message[0] == message[-1] and message[0] in ("'", '"'):
                message = message[1:-1]
            if not message:
                return PlanResult(
                    status=CLARIFICATION_NEEDED,
                    original_text=text,
                    ambiguity="Notification message is empty.",
                    matched_rule=f"{rule_id}.empty",
                )
            return PlanResult(
                status=MAPPED,
                original_text=text,
                capability="desktop.notify",
                parameters={"title": "Jarvis", "message": message},
                confidence=0.9,
                rationale=f"Matched notification phrasing → desktop.notify ({len(message)} chars).",
                matched_rule=rule_id,
            )
        return None

    def _rule_foreground_window(self, text: str, lower: str) -> Optional[PlanResult]:
        # "show my current window", "what window is open", "what's my foreground window",
        # "current window", "what am I looking at"
        patterns = [
            r"^\s*(?:show|tell\s+me|what(?:'s| is))\s+(?:my\s+|the\s+)?"
            r"(?:current|foreground|active)\s+window\s*\??\s*$",
            r"^\s*what\s+window\s+(?:is\s+)?(?:open|active|in\s+front)\s*\??\s*$",
            r"^\s*(?:current|foreground|active)\s+window\s*\??\s*$",
            r"^\s*what\s+am\s+i\s+looking\s+at\s*\??\s*$",
        ]
        for pat in patterns:
            if re.match(pat, text, re.IGNORECASE):
                return PlanResult(
                    status=MAPPED,
                    original_text=text,
                    capability="desktop.foreground_window",
                    parameters={},
                    confidence=0.9,
                    rationale="Matched foreground-window query → desktop.foreground_window.",
                    matched_rule="desktop.foreground_window",
                )
        return None

    def _rule_screenshot(self, text: str, lower: str) -> Optional[PlanResult]:
        """Screenshot phrasings → desktop.screenshot_foreground / _full.

        Defaults to the foreground window when the phrasing implies "my
        window" or is otherwise ambiguous. Only maps to the full virtual
        screen when the user explicitly says so (e.g. "full screen",
        "entire desktop", "whole screen").
        """
        foreground_patterns = [
            r"^\s*(?:take|capture|grab)\s+(?:a\s+)?screenshot\s+of\s+(?:my\s+|the\s+)?"
            r"(?:current\s+|active\s+|foreground\s+)?window\s*\??\s*$",
            r"^\s*screenshot\s+(?:my\s+|the\s+)?(?:current\s+|active\s+|foreground\s+)?"
            r"window\s*\??\s*$",
            r"^\s*(?:take|capture|grab)\s+(?:a\s+)?(?:window\s+)?screenshot\s*\??\s*$",
            r"^\s*screenshot\s*\??\s*$",
            r"^\s*what(?:'s| is)\s+on\s+(?:my\s+|the\s+)?(?:screen|window)\s*\??\s*$",
            r"^\s*show\s+(?:me\s+)?(?:my\s+|the\s+)?(?:current\s+)?screen\s*\??\s*$",
        ]
        full_patterns = [
            r"^\s*(?:take|capture|grab)\s+(?:a\s+)?"
            r"(?:full\s+screen|entire\s+(?:screen|desktop)|whole\s+(?:screen|desktop))"
            r"\s+screenshot\s*\??\s*$",
            r"^\s*(?:take|capture|grab)\s+(?:a\s+)?screenshot\s+of\s+(?:my\s+|the\s+)?"
            r"(?:full(?:\s+screen)?|entire\s+(?:screen|desktop)|whole\s+(?:screen|desktop)|desktop)\s*\??\s*$",
            r"^\s*(?:capture|screenshot)\s+(?:my\s+|the\s+)?"
            r"(?:full\s+screen|entire\s+(?:screen|desktop)|whole\s+(?:screen|desktop)|desktop)\s*\??\s*$",
            r"^\s*full(?:\s+screen)?\s+screenshot\s*\??\s*$",
        ]
        for pat in full_patterns:
            if re.match(pat, text, re.IGNORECASE):
                return PlanResult(
                    status=MAPPED,
                    original_text=text,
                    capability="desktop.screenshot_full",
                    parameters={},
                    confidence=0.9,
                    rationale="Matched full-screen screenshot phrasing → desktop.screenshot_full.",
                    matched_rule="desktop.screenshot_full",
                )
        for pat in foreground_patterns:
            if re.match(pat, text, re.IGNORECASE):
                return PlanResult(
                    status=MAPPED,
                    original_text=text,
                    capability="desktop.screenshot_foreground",
                    parameters={},
                    confidence=0.9,
                    rationale="Matched foreground-window screenshot phrasing → desktop.screenshot_foreground.",
                    matched_rule="desktop.screenshot_foreground",
                )
        return None

    def _rule_focus_app(self, text: str, lower: str) -> Optional[PlanResult]:
        # "focus notepad", "bring notepad to front", "switch to notepad",
        # "activate notepad"
        m = re.match(
            r"^\s*(?:focus(?:\s+on)?|switch\s+to|activate|bring)\s+"
            r"(?:the\s+)?(.+?)(?:\s+to\s+(?:the\s+)?front)?\s*$",
            text, re.IGNORECASE,
        )
        if not m:
            return None
        target_raw = m.group(1).strip().rstrip(".,;")
        target_lower = target_raw.lower()
        app = _app_alias(target_lower)
        if app is not None:
            return PlanResult(
                status=MAPPED,
                original_text=text,
                capability="app.focus",
                parameters={"name": app},
                confidence=0.95,
                rationale=f"Matched 'focus <allowlisted app>' → app.focus ({app}).",
                matched_rule="desktop.focus.app",
            )
        if target_lower in _VAGUE_TARGETS:
            return PlanResult(
                status=CLARIFICATION_NEEDED,
                original_text=text,
                ambiguity=f"'focus {target_raw}' — name the app explicitly.",
                matched_rule="desktop.focus.deictic",
            )
        return PlanResult(
            status=CLARIFICATION_NEEDED,
            original_text=text,
            ambiguity=(
                f"'focus {target_raw}' — {target_raw!r} is not an allowlisted app "
                f"({sorted(APP_ALLOWLIST)})."
            ),
            matched_rule="desktop.focus.unknown_target",
        )

    def _rule_navigate_verbs(self, text: str, lower: str) -> Optional[PlanResult]:
        # "go to <url>" / "visit <url>" / "navigate to <url>" / "browse to <url>"
        m = re.match(rf"^\s*{_NAV_VERBS}\s+(.+?)\s*$", text, re.IGNORECASE)
        if not m:
            return None
        target = m.group(1).strip().rstrip(".,;")
        if target.lower() in _VAGUE_TARGETS:
            return PlanResult(
                status=CLARIFICATION_NEEDED,
                original_text=text,
                ambiguity="Navigation target is deictic; supply a URL.",
                matched_rule="nav.deictic",
            )
        if _looks_like_url(target):
            url = _normalize_url(target)
            return PlanResult(
                status=MAPPED,
                original_text=text,
                capability="browser.navigate",
                parameters={"url": url},
                confidence=0.9,
                rationale=f"Matched navigation verb → browser.navigate ({url}).",
                matched_rule="nav.url",
            )
        return PlanResult(
            status=CLARIFICATION_NEEDED,
            original_text=text,
            ambiguity=(
                f"Navigation target {target!r} is not a URL. Include a scheme "
                "(https://…) or a TLD."
            ),
            matched_rule="nav.bad_target",
        )
