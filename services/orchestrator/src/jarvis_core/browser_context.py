"""Thread-safe, in-process browser context.

The `BrowserContext` holds the assistant's current understanding of what
the user is looking at on the web. It is populated in exactly two ways:

  1. When the guarded ``browser.read_page`` or ``browser.summarize``
     capability fetches a URL, the adapter records the result here.
  2. When the HUD explicitly pushes a snapshot via
     ``POST /browser/snapshot`` (user-initiated — the HUD never does
     this behind the user's back).

Nothing reads the user's real browser tabs in this checkpoint. There is
no DOM scripting, no auto-click, no form submission, no background
polling. The context is an honest, explicit mirror of what the guarded
adapter has fetched (or what the user has explicitly supplied).

If you want to know whether the assistant has any page awareness at a
given moment, call ``has_context()`` — we never fabricate one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, Optional


_MAX_EXCERPT_CHARS = 4000
_MAX_TITLE_CHARS = 300


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _trim(value: Optional[str], limit: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


@dataclass
class BrowserContext:
    """Single-page, single-user, in-memory browser context.

    Not persisted. Not shared across processes. Cleared on restart.
    """

    url: Optional[str] = None
    title: Optional[str] = None
    text_excerpt: Optional[str] = None
    byte_count: int = 0
    source: Optional[str] = None  # e.g. 'browser.read_page', 'hud.snapshot'
    updated_at: Optional[str] = None

    def __post_init__(self) -> None:
        self._lock = Lock()

    # ------------------------------------------------------------------
    def has_context(self) -> bool:
        with self._lock:
            return bool(self.url)

    def snapshot(self) -> Optional[Dict[str, Any]]:
        """Return a JSON-safe view, or None when no context has been recorded."""
        with self._lock:
            if not self.url:
                return None
            return {
                "url": self.url,
                "title": self.title,
                "textExcerpt": self.text_excerpt,
                "byteCount": self.byte_count,
                "source": self.source,
                "updatedAt": self.updated_at,
            }

    def record_page(
        self,
        *,
        url: str,
        title: Optional[str] = None,
        text_excerpt: Optional[str] = None,
        byte_count: int = 0,
        source: str = "browser.read_page",
    ) -> Dict[str, Any]:
        """Record a fetched page. Caller owns URL validation — we don't fetch."""
        if not isinstance(url, str) or not url.strip():
            raise ValueError("Cannot record browser context without a URL.")
        with self._lock:
            self.url = url.strip()
            self.title = _trim(title, _MAX_TITLE_CHARS)
            self.text_excerpt = _trim(text_excerpt, _MAX_EXCERPT_CHARS)
            self.byte_count = int(byte_count or 0)
            self.source = source
            self.updated_at = _now_iso()
            return self._unlocked_snapshot()

    def clear(self) -> None:
        with self._lock:
            self.url = None
            self.title = None
            self.text_excerpt = None
            self.byte_count = 0
            self.source = None
            self.updated_at = _now_iso()

    # ------------------------------------------------------------------
    def _unlocked_snapshot(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "textExcerpt": self.text_excerpt,
            "byteCount": self.byte_count,
            "source": self.source,
            "updatedAt": self.updated_at,
        }
