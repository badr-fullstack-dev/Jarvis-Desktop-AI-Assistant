"""Local OCR providers.

The OCR layer takes a PNG byte string (as produced by the screenshot
adapter) and returns structured text. The contract lives in this module
so capabilities/desktop.py never imports a real OCR library directly —
all OCR is routed through a provider object that the capability holds.

Guarantees
----------
- No provider in this module performs network I/O.
- No provider opens a screen capture device or grabs the screen on its
  own. PNG bytes always arrive from the caller (which itself runs
  through ActionGateway + PolicyEngine).
- Missing dependencies raise :class:`OCRError` with setup instructions;
  the default :class:`UnavailableOCRProvider` returns ``available()`` ==
  False and refuses to fabricate text. Cloud OCR is intentionally not
  implemented — if you add it later, document the privacy properties
  inline before it becomes selectable.

Selection
---------
Set ``JARVIS_OCR_PROVIDER`` to one of:

* ``unavailable`` (default) — explicit "OCR is not configured" mode.
  Every OCR action fails with a clear remediation hint.
* ``windows-media-ocr`` — local Windows.Media.Ocr via the ``winsdk``
  Python package. ``pip install winsdk`` + at least one Windows OCR
  language pack are required.
* ``auto`` — try ``windows-media-ocr`` first, fall back to
  ``unavailable`` if winsdk / language packs are not present. Reports
  the actual chosen provider through ``name``.

Optional: ``JARVIS_OCR_LANGUAGE`` (BCP-47 tag, e.g. ``en-US``) hints the
preferred recognizer language. Defaults to the first installed language
on the user profile.
"""

from __future__ import annotations

import asyncio
import os
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple


class OCRError(RuntimeError):
    """Raised by an OCR provider when it cannot fulfil a request.

    The message is always actionable (what failed, how to fix). The
    capability layer turns this into a ``failed`` ActionResult.
    """


@dataclass(slots=True)
class OCRLine:
    text: str
    confidence: Optional[float] = None  # null when the provider doesn't expose it

    def to_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "confidence": self.confidence}


@dataclass(slots=True)
class OCRResult:
    """Structured OCR output. Always serialisable, always honest."""

    text: str
    lines: List[OCRLine] = field(default_factory=list)
    language: Optional[str] = None
    average_confidence: Optional[float] = None  # null when not exposed by provider
    provider: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "lines": [ln.to_dict() for ln in self.lines],
            "language": self.language,
            "averageConfidence": self.average_confidence,
            "provider": self.provider,
        }


class OCRProvider(ABC):
    """Contract for any local OCR backend."""

    name: str = "abstract"

    @abstractmethod
    def available(self) -> bool:
        """Return True iff :meth:`extract` is wired up to do real work.

        Providers SHOULD perform any cheap availability check here
        (e.g. import the backing library) and cache the result. Heavy
        work belongs in :meth:`extract`.
        """

    @abstractmethod
    def extract(self, png_bytes: bytes, *, language: Optional[str] = None) -> OCRResult:
        """Run OCR on ``png_bytes`` and return a populated :class:`OCRResult`.

        On failure, raise :class:`OCRError` with a clear, actionable
        message — never silently return placeholder text.
        """


class UnavailableOCRProvider(OCRProvider):
    """Default provider — there is no real OCR engine configured.

    This provider does NOT fabricate text. Every call raises
    :class:`OCRError`. The capability layer turns that into a
    ``failed`` ActionResult with a remediation hint pointing at
    ``JARVIS_OCR_PROVIDER`` and the README.
    """

    name = "unavailable"

    def __init__(self, reason: Optional[str] = None) -> None:
        self._reason = reason or (
            "OCR provider is not configured. Set JARVIS_OCR_PROVIDER="
            "windows-media-ocr (after `pip install winsdk` and installing a "
            "Windows OCR language pack) or =auto to enable local OCR. See "
            "the README's 'Local OCR (v1)' section for setup."
        )

    def available(self) -> bool:
        return False

    def extract(self, png_bytes: bytes, *, language: Optional[str] = None) -> OCRResult:
        raise OCRError(self._reason)


# ---------------------------------------------------------------------------
# Windows.Media.Ocr (winsdk)
# ---------------------------------------------------------------------------


class WindowsMediaOCRProvider(OCRProvider):
    """Local OCR backed by Windows.Media.Ocr through the ``winsdk`` package.

    Privacy
    -------
    Windows.Media.Ocr is fully on-device. It uses the OCR language packs
    installed in the user profile. No image bytes leave the machine.

    Limitations
    -----------
    * Windows.Media.Ocr does not expose per-word or per-line
      confidence; ``OCRLine.confidence`` and ``OCRResult.average_confidence``
      are always None for this provider — that is honest, not a bug.
    * Recognition quality depends on the installed language packs.
      ``OcrEngine.try_create_from_user_profile_languages()`` returns
      None when no compatible language is installed; we surface a clear
      error in that case.
    * Accepts arbitrary bitmap formats (winsdk decodes the PNG via
      ``BitmapDecoder``); we always feed PNG bytes since that is what
      the screenshot adapter produces.
    """

    name = "windows-media-ocr"

    def __init__(self, *, language: Optional[str] = None) -> None:
        self._language = language or os.environ.get("JARVIS_OCR_LANGUAGE") or None
        # Lazy import + cache result. The provider object is cheap to construct;
        # we only attempt the import when somebody asks "are you available?"
        # or tries to extract.
        self._import_error: Optional[str] = None
        self._winsdk: Any = None  # populated on first import
        # winsdk async APIs need an event loop; share one per provider.
        self._loop_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    def _ensure_winsdk(self) -> None:
        if self._winsdk is not None or self._import_error is not None:
            return
        try:
            import winsdk  # noqa: F401  (presence check)
            from winsdk.windows.graphics.imaging import BitmapDecoder  # noqa: F401
            from winsdk.windows.media.ocr import OcrEngine  # noqa: F401
            from winsdk.windows.security.cryptography import CryptographicBuffer  # noqa: F401
            from winsdk.windows.storage.streams import InMemoryRandomAccessStream  # noqa: F401
        except Exception as exc:  # broad — winsdk import surfaces many errors
            self._import_error = (
                f"winsdk is not importable ({exc}). Install it with "
                "`pip install winsdk` and ensure you are running on Windows "
                "with at least one OCR language pack installed (Settings → "
                "Time & language → Language → Add a language → Optional "
                "language features → Add 'Optical character recognition')."
            )
            return
        # Keep references — lookups by attribute are cheap.
        import winsdk.windows.graphics.imaging as imaging
        import winsdk.windows.media.ocr as ocr
        import winsdk.windows.security.cryptography as crypto
        import winsdk.windows.storage.streams as streams
        try:
            from winsdk.windows.globalization import Language as _Language  # type: ignore
        except Exception:
            _Language = None
        self._winsdk = {
            "BitmapDecoder": imaging.BitmapDecoder,
            "OcrEngine": ocr.OcrEngine,
            "CryptographicBuffer": crypto.CryptographicBuffer,
            "InMemoryRandomAccessStream": streams.InMemoryRandomAccessStream,
            "Language": _Language,
        }

    def available(self) -> bool:
        self._ensure_winsdk()
        return self._winsdk is not None

    # ------------------------------------------------------------------
    def _get_loop(self) -> asyncio.AbstractEventLoop:
        with self._loop_lock:
            if self._loop is None or self._loop.is_closed():
                self._loop = asyncio.new_event_loop()
            return self._loop

    def extract(self, png_bytes: bytes, *, language: Optional[str] = None) -> OCRResult:
        self._ensure_winsdk()
        if self._winsdk is None:
            raise OCRError(self._import_error or "winsdk is not available.")
        if not png_bytes:
            raise OCRError("OCR input is empty (0 bytes).")
        lang = language or self._language

        try:
            text, lines, lang_tag = self._get_loop().run_until_complete(
                self._extract_async(png_bytes, lang)
            )
        except OCRError:
            raise
        except Exception as exc:  # surface as OCRError with context
            raise OCRError(f"Windows.Media.Ocr failed: {exc}") from exc

        return OCRResult(
            text=text,
            lines=[OCRLine(text=ln, confidence=None) for ln in lines],
            language=lang_tag,
            average_confidence=None,  # Windows.Media.Ocr does not expose it
            provider=self.name,
        )

    async def _extract_async(self, png_bytes: bytes, language: Optional[str]) -> Tuple[str, List[str], Optional[str]]:
        sdk = self._winsdk
        BitmapDecoder = sdk["BitmapDecoder"]
        OcrEngine = sdk["OcrEngine"]
        CryptographicBuffer = sdk["CryptographicBuffer"]
        InMemoryRandomAccessStream = sdk["InMemoryRandomAccessStream"]
        Language = sdk["Language"]

        # Stream the PNG bytes into an in-memory random-access stream.
        buf = CryptographicBuffer.create_from_byte_array(png_bytes)
        stream = InMemoryRandomAccessStream()
        await stream.write_async(buf)
        stream.seek(0)

        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()

        engine = None
        lang_obj = None
        if language and Language is not None:
            try:
                lang_obj = Language(language)
            except Exception:
                lang_obj = None
        if lang_obj is not None:
            try:
                if OcrEngine.is_language_supported(lang_obj):
                    engine = OcrEngine.try_create_from_language(lang_obj)
            except Exception:
                engine = None
        if engine is None:
            engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise OCRError(
                "No Windows OCR language pack is installed for this user "
                "profile. Add one via Settings → Time & language → Language "
                "→ Add a language → Optional language features → 'Optical "
                "character recognition'."
            )
        result = await engine.recognize_async(bitmap)
        full_text = result.text or ""
        lines: List[str] = []
        for line in (result.lines or []):
            line_text = getattr(line, "text", None)
            if line_text:
                lines.append(line_text)
        # Recognizer language tag (best-effort — older winsdk shapes vary).
        lang_tag: Optional[str] = None
        try:
            recog_lang = getattr(engine, "recognizer_language", None)
            if recog_lang is not None:
                lang_tag = getattr(recog_lang, "language_tag", None) \
                    or getattr(recog_lang, "language", None)
        except Exception:
            lang_tag = None
        return full_text, lines, lang_tag


# ---------------------------------------------------------------------------
# Composite + builder
# ---------------------------------------------------------------------------


class CompositeOCRProvider(OCRProvider):
    """Try each provider in order until one is ``available()``.

    Records the chosen provider's name in ``self.name`` (joined with
    ``+`` for the chain) so callers can see honestly which backend
    actually ran.
    """

    def __init__(self, providers: Iterable[OCRProvider]) -> None:
        self._providers = [p for p in providers if p is not None]
        if not self._providers:
            raise ValueError("CompositeOCRProvider requires at least one provider.")
        self.name = "+".join(p.name for p in self._providers)

    def available(self) -> bool:
        return any(p.available() for p in self._providers)

    def extract(self, png_bytes: bytes, *, language: Optional[str] = None) -> OCRResult:
        last_error: Optional[Exception] = None
        for p in self._providers:
            if not p.available():
                continue
            try:
                return p.extract(png_bytes, language=language)
            except OCRError as exc:
                last_error = exc
                continue
        raise OCRError(
            f"No OCR provider in chain {self.name!r} could handle the request."
            + (f" Last error: {last_error}" if last_error else "")
        )


def build_ocr_provider_from_env(env: Optional[Dict[str, str]] = None) -> OCRProvider:
    """Construct an OCR provider from environment variables.

    Defaults to :class:`UnavailableOCRProvider` so a fresh checkout has
    *no* OCR until the user opts in. ``auto`` chains windows-media-ocr
    → unavailable, exposing the chain in ``provider.name``.
    """
    env = env if env is not None else os.environ  # type: ignore[assignment]
    selection = (env.get("JARVIS_OCR_PROVIDER") or "unavailable").strip().lower()
    language = env.get("JARVIS_OCR_LANGUAGE") or None

    if selection in ("", "unavailable", "off", "disabled", "none"):
        return UnavailableOCRProvider()
    if selection in ("windows-media-ocr", "winrt", "windows.media.ocr"):
        return WindowsMediaOCRProvider(language=language)
    if selection == "auto":
        return CompositeOCRProvider([
            WindowsMediaOCRProvider(language=language),
            UnavailableOCRProvider(
                reason="auto: no real OCR provider was available; install winsdk."
            ),
        ])
    raise ValueError(
        f"Unknown JARVIS_OCR_PROVIDER={selection!r}. Valid values: "
        "'unavailable', 'windows-media-ocr', 'auto'."
    )
