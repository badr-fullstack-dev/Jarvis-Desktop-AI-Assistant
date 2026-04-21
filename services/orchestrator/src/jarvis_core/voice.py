"""Voice session state machine + transcription provider abstraction.

Design goals
------------
- Push-to-talk only; no always-listening, no hidden background capture.
- The session state is owned by the backend so the HUD can't silently
  advance it — every transition is explicit and visible in /hud-state.
- Transcription is pluggable: HUD-captured audio (or a test harness)
  hands bytes to a provider that returns text.
- The default provider is a stub that is clearly labelled as a stub.
  Real local/cloud providers are added by implementing
  `TranscriptionProvider` and swapping `VoiceSession.provider`.

State machine
-------------
    idle ─────start()────▶ recording
    recording ─stop(audio)▶ transcribing ──provider ok───▶ ready
                                          └─provider fail─▶ error
    ready ────submit()───▶ idle   (task is created by caller)
    ready ────discard()──▶ idle
    any   ────reset()────▶ idle
    error ────reset()────▶ idle
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, Optional


VALID_STATES = ("idle", "recording", "transcribing", "ready", "error")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Provider abstraction
# ---------------------------------------------------------------------------

class TranscriptionProvider(ABC):
    """Minimal abstract provider.

    Implementations may call local binaries (whisper.cpp), Windows SAPI
    dictation, or isolated cloud APIs. The caller owns audio capture
    and passes raw bytes + a MIME type.
    """

    name: str = "abstract"

    @abstractmethod
    def transcribe(self, audio_bytes: bytes, mime: str) -> str:
        """Return a text transcript for the supplied audio bytes.

        May raise `TranscriptionError` on failure.
        """


class TranscriptionError(RuntimeError):
    """Raised when a provider cannot produce a transcript."""


class StubTranscriptionProvider(TranscriptionProvider):
    """Default provider. Returns a clearly-labelled synthetic transcript.

    This is NOT real speech recognition. It exists so the end-to-end
    voice-session plumbing — mic capture -> backend -> provider ->
    transcript preview -> task creation — can be exercised honestly
    before a real provider is wired in.

    You can point it at a canned phrase via `fixed_transcript=...` or
    leave it unset, in which case it echoes the payload size.
    """

    name = "stub"

    def __init__(self, fixed_transcript: Optional[str] = None) -> None:
        self.fixed_transcript = fixed_transcript

    def transcribe(self, audio_bytes: bytes, mime: str) -> str:
        if self.fixed_transcript is not None:
            return self.fixed_transcript
        size = len(audio_bytes or b"")
        return (
            f"[stub transcript — provider=stub, not real speech recognition] "
            f"received {size} bytes of {mime or 'audio/unknown'}"
        )


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

@dataclass
class VoiceSession:
    """Server-side voice session.

    Thread-safe for the simple sequential flow used by the bridge;
    every mutator takes `self._lock`.
    """

    provider: TranscriptionProvider = field(default_factory=StubTranscriptionProvider)
    enabled: bool = True

    # Observable state (never mutated outside a transition method).
    state: str = "idle"
    transcript: Optional[str] = None
    error: Optional[str] = None
    last_audio_bytes: int = 0
    last_mime: Optional[str] = None
    updated_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        self._lock = Lock()

    # ------------------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "state": self.state,
                "enabled": self.enabled,
                "transcript": self.transcript,
                "error": self.error,
                "provider": self.provider.name,
                "lastAudioBytes": self.last_audio_bytes,
                "lastMime": self.last_mime,
                "updatedAt": self.updated_at,
            }

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def start(self) -> Dict[str, Any]:
        with self._lock:
            if not self.enabled:
                raise VoiceError("Voice is disabled.")
            if self.state not in ("idle", "error"):
                raise VoiceError(f"Cannot start recording from state '{self.state}'.")
            self.state = "recording"
            self.transcript = None
            self.error = None
            self.updated_at = _now_iso()
            return self._unlocked_snapshot()

    def stop(self, audio_bytes: bytes, mime: str = "audio/webm") -> Dict[str, Any]:
        with self._lock:
            if self.state != "recording":
                raise VoiceError(f"Cannot stop from state '{self.state}'.")
            self.state = "transcribing"
            self.last_audio_bytes = len(audio_bytes or b"")
            self.last_mime = mime
            self.updated_at = _now_iso()

        # Run provider OUTSIDE the lock so a slow transcription does
        # not block snapshot reads.
        try:
            transcript = self.provider.transcribe(audio_bytes or b"", mime)
            if not isinstance(transcript, str):
                raise TranscriptionError("Provider returned a non-string transcript.")
        except Exception as exc:
            with self._lock:
                self.state = "error"
                self.error = f"transcription failed: {exc}"
                self.updated_at = _now_iso()
                return self._unlocked_snapshot()

        with self._lock:
            self.state = "ready"
            self.transcript = transcript.strip()
            self.updated_at = _now_iso()
            return self._unlocked_snapshot()

    def consume_transcript(self, override: Optional[str] = None) -> str:
        """Return the current transcript and clear the session back to idle.

        Intended for the bridge's `/voice/submit` path, after the caller
        has created a task from the transcript.
        """
        with self._lock:
            if self.state != "ready":
                raise VoiceError(f"No transcript ready; state is '{self.state}'.")
            text = (override if override is not None else self.transcript) or ""
            text = text.strip()
            if not text:
                raise VoiceError("Empty transcript cannot be submitted.")
            self.transcript = None
            self.error = None
            self.state = "idle"
            self.updated_at = _now_iso()
            return text

    def discard(self) -> Dict[str, Any]:
        with self._lock:
            if self.state != "ready":
                raise VoiceError(f"Nothing to discard from state '{self.state}'.")
            self.transcript = None
            self.state = "idle"
            self.updated_at = _now_iso()
            return self._unlocked_snapshot()

    def reset(self) -> Dict[str, Any]:
        with self._lock:
            self.state = "idle"
            self.transcript = None
            self.error = None
            self.updated_at = _now_iso()
            return self._unlocked_snapshot()

    def set_enabled(self, enabled: bool) -> Dict[str, Any]:
        with self._lock:
            self.enabled = bool(enabled)
            if not self.enabled:
                self.state = "idle"
                self.transcript = None
                self.error = None
            self.updated_at = _now_iso()
            return self._unlocked_snapshot()

    # ------------------------------------------------------------------
    def _unlocked_snapshot(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "enabled": self.enabled,
            "transcript": self.transcript,
            "error": self.error,
            "provider": self.provider.name,
            "lastAudioBytes": self.last_audio_bytes,
            "lastMime": self.last_mime,
            "updatedAt": self.updated_at,
        }


class VoiceError(ValueError):
    """Raised when a transition is not valid from the current state."""
