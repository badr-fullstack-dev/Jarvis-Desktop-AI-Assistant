"""Real local speech-to-text providers.

The HUD captures audio with ``MediaRecorder`` (typically webm/opus) and
POSTs the bytes to the orchestrator. These providers turn those bytes
into a transcript *locally* — no cloud, no network — and surface a
clear ``TranscriptionError`` whenever a dependency (python package,
model file, or ``ffmpeg`` binary) is missing, so the HUD can show an
actionable error instead of a silent failure.

Guarantees
----------
- No provider in this module performs any network I/O.
- No provider opens a microphone. Audio always arrives from the HUD
  (push-to-talk) as raw bytes.
- Missing dependencies raise ``TranscriptionError`` with setup
  instructions; they do NOT fall back to a different provider unless
  the caller wraps them in :class:`CompositeTranscriptionProvider`.
- Cloud STT is intentionally NOT implemented here. If you add it
  later, document the privacy impact next to the implementation.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from .voice import (
    StubTranscriptionProvider,
    TranscriptionError,
    TranscriptionProvider,
)


# ---------------------------------------------------------------------------
# Audio decode helper
# ---------------------------------------------------------------------------

def _suffix_for_mime(mime: str) -> str:
    """Guess a filename suffix from the HUD's MIME string.

    The suffix is a *hint* to ffmpeg's demuxer probing. It does not
    change correctness (ffmpeg still probes the container) but it
    makes debug dumps human-readable and nudges probing on borderline
    streams.
    """
    m = (mime or "").lower().split(";", 1)[0].strip()
    if "webm" in m:
        return ".webm"
    if "ogg" in m or "opus" in m:
        return ".ogg"
    if "mp4" in m or "m4a" in m or "aac" in m:
        return ".m4a"
    if "wav" in m or "wave" in m:
        return ".wav"
    if "mpeg" in m or m.endswith("/mp3"):
        return ".mp3"
    return ".bin"


def _decode_to_wav_bytes(
    audio_bytes: bytes,
    mime: str,
    ffmpeg_path: str = "ffmpeg",
    *,
    sample_rate: int = 16000,
    timeout: float = 30.0,
    debug_dump_dir: Optional[str] = None,
) -> bytes:
    """Decode arbitrary HUD audio (typically webm/opus) to 16kHz mono WAV.

    The audio is written to a short-lived temp file and read back via
    ``-i <path>``, NOT piped on stdin. MediaRecorder emits WebM with
    unknown-size clusters that ffmpeg can only parse on a *seekable*
    input; feeding it through ``pipe:0`` triggers errors like
    ``"0x00 at pos N invalid as first byte of an EBML number"`` /
    ``"End of file"`` on Chromium-based engines including WebView2.

    If decoding fails and ``debug_dump_dir`` is provided, the exact
    bytes received are written to ``<dir>/failed-<ts>.<ext>`` so the
    failure can be reproduced offline. The dump is the only disk
    residue — the temp input file is always deleted.
    """
    if not audio_bytes:
        raise TranscriptionError("Empty audio input — nothing to decode.")

    resolved = shutil.which(ffmpeg_path) or (ffmpeg_path if os.path.isabs(ffmpeg_path) and Path(ffmpeg_path).exists() else None)
    if resolved is None:
        raise TranscriptionError(
            f"ffmpeg not found (looked for {ffmpeg_path!r}). Install ffmpeg "
            f"(https://ffmpeg.org/download.html) and make sure it is on PATH, "
            f"or set JARVIS_FFMPEG to its absolute path."
        )

    suffix = _suffix_for_mime(mime)
    in_fd, in_path = tempfile.mkstemp(suffix=suffix, prefix="jarvis_stt_in_")
    try:
        with os.fdopen(in_fd, "wb") as fh:
            fh.write(audio_bytes)

        try:
            proc = subprocess.run(
                [
                    resolved, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", in_path,
                    "-ac", "1", "-ar", str(sample_rate),
                    "-f", "wav", "pipe:1",
                ],
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise TranscriptionError(f"ffmpeg timed out after {timeout}s decoding {mime!r}.") from exc
        except OSError as exc:
            raise TranscriptionError(f"Failed to invoke ffmpeg: {exc}") from exc

        if proc.returncode != 0 or not proc.stdout:
            stderr = (proc.stderr or b"").decode("utf-8", "replace").strip()
            dump_note = ""
            if debug_dump_dir:
                try:
                    dump_dir = Path(debug_dump_dir)
                    dump_dir.mkdir(parents=True, exist_ok=True)
                    import time
                    dump_path = dump_dir / f"failed-{int(time.time())}{suffix}"
                    dump_path.write_bytes(audio_bytes)
                    dump_note = f" (audio saved to {dump_path})"
                except OSError:
                    pass
            detail = stderr[:500] or "unknown error"
            raise TranscriptionError(
                f"ffmpeg failed to decode {mime!r} audio ({len(audio_bytes)} bytes): {detail}{dump_note}"
            )
        return proc.stdout
    finally:
        try:
            os.unlink(in_path)
        except OSError:
            pass


def _write_temp_wav(wav_bytes: bytes) -> str:
    """Write WAV bytes to a temp file. Windows-safe (closes before return)."""
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="jarvis_stt_")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(wav_bytes)
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path


# ---------------------------------------------------------------------------
# faster-whisper provider (primary local option)
# ---------------------------------------------------------------------------

class FasterWhisperProvider(TranscriptionProvider):
    """Local CPU-friendly transcription via ``faster-whisper``.

    Runs fully offline once the model is downloaded. The first call
    downloads the model from Hugging Face (one-time); subsequent
    calls use the cached copy under ``model_dir`` (or the default
    ``~/.cache/huggingface``).

    All heavy work is lazy: no model is loaded until ``transcribe()``
    is called. This keeps orchestrator startup fast and lets the HUD
    show a clean "provider not ready" error if the model or the
    ``faster-whisper`` package is missing.
    """

    name = "faster-whisper"

    def __init__(
        self,
        model_name: str = "base.en",
        model_dir: Optional[str] = None,
        compute_type: str = "int8",
        device: str = "cpu",
        language: Optional[str] = "en",
        ffmpeg_path: str = "ffmpeg",
        debug_dump_dir: Optional[str] = None,
        *,
        _model: Any = None,
        _decoder: Optional[Callable[[bytes, str], bytes]] = None,
    ) -> None:
        self._model_name = model_name
        self._model_dir = model_dir
        self._compute_type = compute_type
        self._device = device
        self._language = language
        self._ffmpeg_path = ffmpeg_path
        self._debug_dump_dir = debug_dump_dir
        self._model = _model
        self._decoder = _decoder

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:
            raise TranscriptionError(
                "faster-whisper is not installed. Run "
                "`pip install faster-whisper` (and ensure ffmpeg is on PATH) "
                "or switch JARVIS_STT_PROVIDER back to 'stub'."
            ) from exc
        try:
            self._model = WhisperModel(
                self._model_name,
                device=self._device,
                compute_type=self._compute_type,
                download_root=self._model_dir,
            )
        except Exception as exc:  # pragma: no cover - surfaced as TranscriptionError
            raise TranscriptionError(
                f"Could not load faster-whisper model {self._model_name!r} "
                f"(compute_type={self._compute_type!r}, device={self._device!r}): {exc}"
            ) from exc
        return self._model

    def transcribe(self, audio_bytes: bytes, mime: str) -> str:
        if not isinstance(audio_bytes, (bytes, bytearray)) or not audio_bytes:
            raise TranscriptionError("Empty or invalid audio input.")

        decode = self._decoder or (lambda b, m: _decode_to_wav_bytes(
            b, m, self._ffmpeg_path, debug_dump_dir=self._debug_dump_dir,
        ))
        wav_bytes = decode(audio_bytes, mime)
        if not isinstance(wav_bytes, (bytes, bytearray)) or not wav_bytes:
            raise TranscriptionError("Audio decoder produced no WAV data.")

        model = self._load_model()
        wav_path = _write_temp_wav(wav_bytes)
        try:
            try:
                segments, _info = model.transcribe(
                    wav_path,
                    language=self._language,
                    beam_size=1,
                    vad_filter=False,
                )
            except Exception as exc:
                raise TranscriptionError(f"faster-whisper transcription failed: {exc}") from exc
            parts = [str(getattr(seg, "text", "") or "").strip() for seg in segments]
            return " ".join(p for p in parts if p).strip()
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# whisper.cpp provider (alternative local option)
# ---------------------------------------------------------------------------

def _default_whispercpp_runner(
    binary: str, model_path: str, wav_path: str, language: Optional[str]
) -> str:
    """Invoke whisper.cpp and return the transcript text (no timestamps)."""
    cmd = [binary, "-m", model_path, "-f", wav_path, "-nt", "-np"]
    if language:
        cmd.extend(["-l", language])
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise TranscriptionError("whisper.cpp timed out after 120s.") from exc
    except OSError as exc:
        raise TranscriptionError(f"Failed to run whisper.cpp binary {binary!r}: {exc}") from exc
    if proc.returncode != 0:
        stderr = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise TranscriptionError(
            f"whisper.cpp exited with code {proc.returncode}: {stderr[:500] or 'unknown error'}"
        )
    text = (proc.stdout or b"").decode("utf-8", "replace").strip()
    # whisper.cpp may emit leading spaces / blank lines; collapse them.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return " ".join(lines).strip()


class WhisperCppProvider(TranscriptionProvider):
    """Local transcription via an external ``whisper.cpp`` build.

    You supply the absolute path to the CLI binary (``main.exe`` /
    ``whisper-cli.exe``) and to a GGML/GGUF model file. Nothing is
    downloaded automatically — the provider only raises a clear error
    when a path is missing or the binary/model is not on disk.
    """

    name = "whisper.cpp"

    def __init__(
        self,
        binary: Optional[str] = None,
        model_path: Optional[str] = None,
        ffmpeg_path: str = "ffmpeg",
        language: Optional[str] = "en",
        debug_dump_dir: Optional[str] = None,
        *,
        _runner: Optional[Callable[[str, str, str, Optional[str]], str]] = None,
        _decoder: Optional[Callable[[bytes, str], bytes]] = None,
    ) -> None:
        self._binary = binary
        self._model_path = model_path
        self._ffmpeg_path = ffmpeg_path
        self._language = language
        self._debug_dump_dir = debug_dump_dir
        self._runner = _runner
        self._decoder = _decoder

    def _check_config(self) -> None:
        # Injected runner means tests bypass the real binary/model. That's fine.
        if self._runner is not None:
            return
        if not self._binary:
            raise TranscriptionError(
                "whisper.cpp binary path not configured. Set JARVIS_WHISPERCPP_BIN to the "
                "absolute path of main.exe / whisper-cli.exe."
            )
        if not Path(self._binary).exists():
            raise TranscriptionError(f"whisper.cpp binary not found: {self._binary}")
        if not self._model_path:
            raise TranscriptionError(
                "whisper.cpp model path not configured. Set JARVIS_STT_MODEL to the "
                "absolute path of the .bin model (e.g. ggml-base.en.bin)."
            )
        if not Path(self._model_path).exists():
            raise TranscriptionError(f"whisper.cpp model not found: {self._model_path}")

    def transcribe(self, audio_bytes: bytes, mime: str) -> str:
        if not isinstance(audio_bytes, (bytes, bytearray)) or not audio_bytes:
            raise TranscriptionError("Empty or invalid audio input.")
        self._check_config()

        decode = self._decoder or (lambda b, m: _decode_to_wav_bytes(
            b, m, self._ffmpeg_path, debug_dump_dir=self._debug_dump_dir,
        ))
        wav_bytes = decode(audio_bytes, mime)
        if not isinstance(wav_bytes, (bytes, bytearray)) or not wav_bytes:
            raise TranscriptionError("Audio decoder produced no WAV data.")

        wav_path = _write_temp_wav(wav_bytes)
        try:
            runner = self._runner or _default_whispercpp_runner
            text = runner(self._binary or "", self._model_path or "", wav_path, self._language)
            return (text or "").strip()
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Fallback chain
# ---------------------------------------------------------------------------

class CompositeTranscriptionProvider(TranscriptionProvider):
    """Try providers in order; return the first successful transcript.

    Only :class:`TranscriptionError` is caught — any other exception
    propagates immediately so unexpected bugs are not silently swallowed.

    The ``name`` attribute concatenates member names (e.g.
    ``"faster-whisper+stub"``) so the HUD's "provider" label shows the
    chain honestly.
    """

    def __init__(self, providers: Iterable[TranscriptionProvider]) -> None:
        self._providers = [p for p in providers if p is not None]
        if not self._providers:
            raise ValueError("CompositeTranscriptionProvider requires at least one provider.")
        self.name = "+".join(p.name for p in self._providers)

    def transcribe(self, audio_bytes: bytes, mime: str) -> str:
        errors = []
        for provider in self._providers:
            try:
                return provider.transcribe(audio_bytes, mime)
            except TranscriptionError as exc:
                errors.append(f"{provider.name}: {exc}")
        raise TranscriptionError(
            "All transcription providers failed — " + " | ".join(errors)
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_DEFAULT_PROVIDER = "stub"


def build_provider_from_env(
    env: Optional[Mapping[str, str]] = None,
) -> TranscriptionProvider:
    """Build a transcription provider from environment variables.

    Recognised variables:

    - ``JARVIS_STT_PROVIDER`` — ``stub`` (default), ``faster-whisper``,
      ``whisper.cpp``, or ``auto`` (faster-whisper with a stub fallback,
      and the provider name clearly reflects the chain).
    - ``JARVIS_STT_MODEL`` — model name for faster-whisper (e.g.
      ``base.en``, ``small.en``), or absolute model path for whisper.cpp.
    - ``JARVIS_STT_MODEL_DIR`` — optional local cache directory for
      faster-whisper model downloads.
    - ``JARVIS_STT_COMPUTE`` — faster-whisper compute type (default
      ``int8``; use ``int8_float16`` / ``float16`` on GPU).
    - ``JARVIS_STT_DEVICE`` — ``cpu`` (default) or ``cuda``.
    - ``JARVIS_STT_LANGUAGE`` — ISO code (default ``en``). Set to empty
      string for auto-detect on multilingual models.
    - ``JARVIS_FFMPEG`` — override ffmpeg path (default ``ffmpeg``).
    - ``JARVIS_WHISPERCPP_BIN`` — absolute path to whisper.cpp CLI.

    Unknown values raise ``ValueError`` rather than silently falling
    back to a different provider.
    """
    env = os.environ if env is None else env
    raw = (env.get("JARVIS_STT_PROVIDER") or _DEFAULT_PROVIDER).strip().lower()
    language = env.get("JARVIS_STT_LANGUAGE", "en")
    language = language if language else None
    ffmpeg_path = env.get("JARVIS_FFMPEG", "ffmpeg")
    debug_dump_dir = env.get("JARVIS_STT_DEBUG_DIR") or None

    if raw in ("", "stub", "none", "off"):
        return StubTranscriptionProvider()

    if raw in ("faster-whisper", "fasterwhisper", "whisper", "faster_whisper"):
        return FasterWhisperProvider(
            model_name=env.get("JARVIS_STT_MODEL") or "base.en",
            model_dir=env.get("JARVIS_STT_MODEL_DIR") or None,
            compute_type=env.get("JARVIS_STT_COMPUTE") or "int8",
            device=env.get("JARVIS_STT_DEVICE") or "cpu",
            language=language,
            ffmpeg_path=ffmpeg_path,
            debug_dump_dir=debug_dump_dir,
        )

    if raw in ("whisper.cpp", "whispercpp", "whisper-cpp"):
        return WhisperCppProvider(
            binary=env.get("JARVIS_WHISPERCPP_BIN") or None,
            model_path=env.get("JARVIS_STT_MODEL") or None,
            ffmpeg_path=ffmpeg_path,
            language=language,
            debug_dump_dir=debug_dump_dir,
        )

    if raw == "auto":
        fw = FasterWhisperProvider(
            model_name=env.get("JARVIS_STT_MODEL") or "base.en",
            model_dir=env.get("JARVIS_STT_MODEL_DIR") or None,
            compute_type=env.get("JARVIS_STT_COMPUTE") or "int8",
            device=env.get("JARVIS_STT_DEVICE") or "cpu",
            language=language,
            ffmpeg_path=ffmpeg_path,
            debug_dump_dir=debug_dump_dir,
        )
        return CompositeTranscriptionProvider([fw, StubTranscriptionProvider()])

    raise ValueError(
        f"Unknown JARVIS_STT_PROVIDER={raw!r}. Expected one of: "
        f"stub, faster-whisper, whisper.cpp, auto."
    )
