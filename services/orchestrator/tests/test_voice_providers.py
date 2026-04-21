"""Tests for the real local transcription providers.

These tests never download a model, never invoke ``faster_whisper``,
never open a microphone, and never require ``ffmpeg`` on PATH. All
heavy dependencies are injected.

They cover:
  - a successful transcription path (through FasterWhisperProvider with
    an injected fake model),
  - a successful transcription path (through WhisperCppProvider with an
    injected runner),
  - missing-package / missing-model / missing-binary failure modes,
  - invalid audio input (empty bytes, non-bytes, decoder failure),
  - the environment factory (build_provider_from_env) including the
    'auto' fallback chain,
  - that the composite fallback returns the first success and surfaces
    all errors when every provider fails.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, List, Optional
from unittest import mock

from src.jarvis_core.voice import (
    StubTranscriptionProvider,
    TranscriptionError,
    TranscriptionProvider,
)
from src.jarvis_core.voice_providers import (
    CompositeTranscriptionProvider,
    FasterWhisperProvider,
    WhisperCppProvider,
    _decode_to_wav_bytes,
    _suffix_for_mime,
    build_provider_from_env,
)


# ---------------------------------------------------------------------------
# Fakes used throughout
# ---------------------------------------------------------------------------

class _FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeFasterWhisperModel:
    """Stand-in for ``faster_whisper.WhisperModel`` — records the path it
    was handed and returns a scripted list of segments."""

    def __init__(self, segments_text: List[str]) -> None:
        self._segments = [_FakeSegment(t) for t in segments_text]
        self.calls: List[tuple] = []

    def transcribe(self, audio_path, **kwargs):
        self.calls.append((audio_path, kwargs))
        # faster-whisper returns a generator + an info tuple.
        return iter(self._segments), {"language": kwargs.get("language")}


def _fake_decoder(expected_bytes: bytes, out: bytes = b"RIFFxxxxWAVEfakedata"):
    def _decode(audio_bytes: bytes, mime: str) -> bytes:
        assert audio_bytes == expected_bytes
        return out
    return _decode


# ---------------------------------------------------------------------------
# FasterWhisperProvider
# ---------------------------------------------------------------------------

class FasterWhisperProviderTests(unittest.TestCase):

    def test_name_is_stable(self) -> None:
        self.assertEqual(FasterWhisperProvider.name, "faster-whisper")
        self.assertEqual(FasterWhisperProvider().name, "faster-whisper")

    def test_successful_transcription_uses_model_and_deletes_temp_wav(self) -> None:
        model = _FakeFasterWhisperModel(["  hello world  ", " how are you "])
        p = FasterWhisperProvider(
            _model=model,
            _decoder=_fake_decoder(b"raw-audio"),
        )
        text = p.transcribe(b"raw-audio", mime="audio/webm")
        self.assertEqual(text, "hello world how are you")
        self.assertEqual(len(model.calls), 1)
        wav_path, kwargs = model.calls[0]
        self.assertTrue(wav_path.endswith(".wav"))
        # Temp file should be cleaned up after transcribe returns.
        self.assertFalse(os.path.exists(wav_path))
        self.assertEqual(kwargs.get("language"), "en")
        self.assertEqual(kwargs.get("beam_size"), 1)

    def test_empty_audio_is_rejected_before_any_expensive_work(self) -> None:
        model = _FakeFasterWhisperModel(["ignored"])
        p = FasterWhisperProvider(_model=model, _decoder=_fake_decoder(b""))
        with self.assertRaises(TranscriptionError) as ctx:
            p.transcribe(b"", mime="audio/webm")
        self.assertIn("Empty", str(ctx.exception))
        # Decoder / model must NOT have been called.
        self.assertEqual(model.calls, [])

    def test_non_bytes_audio_is_rejected(self) -> None:
        p = FasterWhisperProvider(_model=_FakeFasterWhisperModel(["x"]))
        with self.assertRaises(TranscriptionError):
            p.transcribe("not-bytes", mime="audio/webm")  # type: ignore[arg-type]

    def test_decoder_failure_surfaces_as_transcription_error(self) -> None:
        def broken_decoder(_b, _m):
            raise TranscriptionError("ffmpeg not found (looked for 'ffmpeg').")
        p = FasterWhisperProvider(
            _model=_FakeFasterWhisperModel(["never-used"]),
            _decoder=broken_decoder,
        )
        with self.assertRaises(TranscriptionError) as ctx:
            p.transcribe(b"\x00\x01", mime="audio/webm")
        self.assertIn("ffmpeg", str(ctx.exception))

    def test_missing_faster_whisper_package_raises_actionable_error(self) -> None:
        # No _model injected → the provider will try to import faster_whisper.
        p = FasterWhisperProvider(_decoder=_fake_decoder(b"x"))
        import sys
        with mock.patch.dict(sys.modules, {"faster_whisper": None}):
            # Setting to None makes `import faster_whisper` raise ImportError.
            with self.assertRaises(TranscriptionError) as ctx:
                p.transcribe(b"x", mime="audio/webm")
        msg = str(ctx.exception)
        self.assertIn("faster-whisper", msg)
        self.assertIn("pip install", msg)

    def test_model_transcribe_exception_is_wrapped(self) -> None:
        class _ExplodingModel:
            def transcribe(self, *_a, **_k):
                raise RuntimeError("model corrupt")
        p = FasterWhisperProvider(
            _model=_ExplodingModel(),
            _decoder=_fake_decoder(b"x"),
        )
        with self.assertRaises(TranscriptionError) as ctx:
            p.transcribe(b"x", mime="audio/webm")
        self.assertIn("model corrupt", str(ctx.exception))


# ---------------------------------------------------------------------------
# WhisperCppProvider
# ---------------------------------------------------------------------------

class WhisperCppProviderTests(unittest.TestCase):

    def test_successful_transcription_via_injected_runner(self) -> None:
        seen = {}
        def runner(binary, model_path, wav_path, language):
            seen["binary"] = binary
            seen["model"] = model_path
            seen["wav"] = wav_path
            seen["lang"] = language
            return "  open the installer page  "
        p = WhisperCppProvider(
            binary="C:/fake/main.exe",
            model_path="C:/fake/ggml-base.en.bin",
            _runner=runner,
            _decoder=_fake_decoder(b"audio"),
        )
        text = p.transcribe(b"audio", mime="audio/webm")
        self.assertEqual(text, "open the installer page")
        self.assertEqual(seen["binary"], "C:/fake/main.exe")
        self.assertEqual(seen["lang"], "en")
        self.assertTrue(seen["wav"].endswith(".wav"))
        # Temp wav cleaned up
        self.assertFalse(os.path.exists(seen["wav"]))

    def test_missing_binary_path_raises(self) -> None:
        p = WhisperCppProvider(binary=None, model_path="x.bin")
        with self.assertRaises(TranscriptionError) as ctx:
            p.transcribe(b"audio", mime="audio/webm")
        self.assertIn("binary path", str(ctx.exception))

    def test_missing_model_path_raises(self) -> None:
        # Point the binary at a real existing file so the binary check passes
        # and we exercise the model-missing branch specifically.
        with tempfile.NamedTemporaryFile(delete=False, suffix=".exe") as fh:
            fh.write(b"")
            bin_path = fh.name
        try:
            p = WhisperCppProvider(binary=bin_path, model_path=None)
            with self.assertRaises(TranscriptionError) as ctx:
                p.transcribe(b"audio", mime="audio/webm")
            self.assertIn("model path", str(ctx.exception))
        finally:
            os.unlink(bin_path)

    def test_nonexistent_binary_raises(self) -> None:
        p = WhisperCppProvider(
            binary="C:/definitely/does/not/exist/main.exe",
            model_path="C:/also/not/real.bin",
        )
        with self.assertRaises(TranscriptionError) as ctx:
            p.transcribe(b"audio", mime="audio/webm")
        self.assertIn("binary not found", str(ctx.exception))

    def test_empty_audio_rejected(self) -> None:
        p = WhisperCppProvider(_runner=lambda *a, **k: "x")
        with self.assertRaises(TranscriptionError):
            p.transcribe(b"", mime="audio/webm")


# ---------------------------------------------------------------------------
# ffmpeg decode helper
# ---------------------------------------------------------------------------

class DecodeHelperTests(unittest.TestCase):

    def test_missing_ffmpeg_raises_actionable_error(self) -> None:
        # shutil.which returns None for a sentinel name.
        with self.assertRaises(TranscriptionError) as ctx:
            _decode_to_wav_bytes(
                b"x", "audio/webm",
                ffmpeg_path="jarvis-ffmpeg-that-does-not-exist",
            )
        self.assertIn("ffmpeg not found", str(ctx.exception))
        self.assertIn("JARVIS_FFMPEG", str(ctx.exception))

    def test_empty_audio_rejected(self) -> None:
        with self.assertRaises(TranscriptionError):
            _decode_to_wav_bytes(b"", "audio/webm")

    def test_suffix_for_mime_maps_common_containers(self) -> None:
        self.assertEqual(_suffix_for_mime("audio/webm;codecs=opus"), ".webm")
        self.assertEqual(_suffix_for_mime("audio/webm"), ".webm")
        self.assertEqual(_suffix_for_mime("audio/ogg"), ".ogg")
        self.assertEqual(_suffix_for_mime("audio/mp4"), ".m4a")
        self.assertEqual(_suffix_for_mime("audio/wav"), ".wav")
        self.assertEqual(_suffix_for_mime(""), ".bin")
        self.assertEqual(_suffix_for_mime("application/octet-stream"), ".bin")

    def test_decoder_invokes_ffmpeg_with_file_input_not_stdin(self) -> None:
        """Regression: MediaRecorder WebM fails on pipe:0 because it is
        not seekable. The decoder MUST pass an on-disk path to -i."""
        captured = {}

        def fake_run(cmd, capture_output, timeout, **_kwargs):
            captured["cmd"] = cmd
            # Verify the input argument is a real file on disk and holds
            # the bytes we supplied — i.e. no pipe:0.
            idx = cmd.index("-i")
            in_path = cmd[idx + 1]
            captured["in_path"] = in_path
            assert in_path != "pipe:0"
            assert Path(in_path).exists()
            assert Path(in_path).read_bytes() == b"mock-webm-bytes"

            class _Result:
                returncode = 0
                stdout = b"RIFF....WAVEfakewavoutput"
                stderr = b""
            return _Result()

        with mock.patch("src.jarvis_core.voice_providers.shutil.which",
                        return_value="C:/fake/ffmpeg.exe"), \
             mock.patch("src.jarvis_core.voice_providers.subprocess.run",
                        side_effect=fake_run):
            out = _decode_to_wav_bytes(
                b"mock-webm-bytes", "audio/webm;codecs=opus",
                ffmpeg_path="ffmpeg",
            )
        self.assertEqual(out, b"RIFF....WAVEfakewavoutput")
        self.assertNotIn("pipe:0", captured["cmd"])
        # Temp file must be deleted afterwards.
        self.assertFalse(Path(captured["in_path"]).exists())
        # Suffix should be .webm to hint ffmpeg's probe.
        self.assertTrue(captured["in_path"].endswith(".webm"))

    def test_decoder_dumps_audio_on_failure_when_debug_dir_set(self) -> None:
        def fake_run(cmd, capture_output, timeout, **_kwargs):
            class _Result:
                returncode = 1
                stdout = b""
                stderr = b"invalid as first byte of an EBML number"
            return _Result()

        with tempfile.TemporaryDirectory() as dump_dir:
            with mock.patch("src.jarvis_core.voice_providers.shutil.which",
                            return_value="C:/fake/ffmpeg.exe"), \
                 mock.patch("src.jarvis_core.voice_providers.subprocess.run",
                            side_effect=fake_run):
                with self.assertRaises(TranscriptionError) as ctx:
                    _decode_to_wav_bytes(
                        b"corrupt-bytes", "audio/webm;codecs=opus",
                        ffmpeg_path="ffmpeg",
                        debug_dump_dir=dump_dir,
                    )
            # Error surfaces the ffmpeg message AND the dump path.
            msg = str(ctx.exception)
            self.assertIn("EBML", msg)
            self.assertIn("audio saved to", msg)
            dumps = list(Path(dump_dir).glob("failed-*.webm"))
            self.assertEqual(len(dumps), 1)
            self.assertEqual(dumps[0].read_bytes(), b"corrupt-bytes")


# ---------------------------------------------------------------------------
# CompositeTranscriptionProvider
# ---------------------------------------------------------------------------

class _AlwaysFails(TranscriptionProvider):
    def __init__(self, name: str, msg: str) -> None:
        self.name = name
        self._msg = msg
    def transcribe(self, audio_bytes: bytes, mime: str) -> str:
        raise TranscriptionError(self._msg)


class _AlwaysSucceeds(TranscriptionProvider):
    def __init__(self, name: str, text: str) -> None:
        self.name = name
        self._text = text
    def transcribe(self, audio_bytes: bytes, mime: str) -> str:
        return self._text


class CompositeProviderTests(unittest.TestCase):

    def test_returns_first_success_and_does_not_call_later_providers(self) -> None:
        first = _AlwaysSucceeds("one", "hello")
        second_called = {"yes": False}
        class _Second(TranscriptionProvider):
            name = "two"
            def transcribe(self, audio_bytes, mime):
                second_called["yes"] = True
                return "nope"
        c = CompositeTranscriptionProvider([first, _Second()])
        self.assertEqual(c.transcribe(b"x", "audio/webm"), "hello")
        self.assertFalse(second_called["yes"])
        self.assertEqual(c.name, "one+two")

    def test_falls_back_to_next_provider_on_transcription_error(self) -> None:
        c = CompositeTranscriptionProvider([
            _AlwaysFails("one", "first broke"),
            _AlwaysSucceeds("two", "recovered"),
        ])
        self.assertEqual(c.transcribe(b"x", "audio/webm"), "recovered")

    def test_all_fail_raises_aggregate_error(self) -> None:
        c = CompositeTranscriptionProvider([
            _AlwaysFails("one", "missing package"),
            _AlwaysFails("two", "missing model"),
        ])
        with self.assertRaises(TranscriptionError) as ctx:
            c.transcribe(b"x", "audio/webm")
        msg = str(ctx.exception)
        self.assertIn("one: missing package", msg)
        self.assertIn("two: missing model", msg)

    def test_non_transcription_errors_propagate(self) -> None:
        class _Kaboom(TranscriptionProvider):
            name = "kaboom"
            def transcribe(self, *_a, **_k):
                raise RuntimeError("unexpected bug")
        c = CompositeTranscriptionProvider([_Kaboom(), _AlwaysSucceeds("two", "x")])
        with self.assertRaises(RuntimeError):
            c.transcribe(b"x", "audio/webm")

    def test_empty_provider_list_rejected(self) -> None:
        with self.assertRaises(ValueError):
            CompositeTranscriptionProvider([])


# ---------------------------------------------------------------------------
# Factory: build_provider_from_env
# ---------------------------------------------------------------------------

class BuildProviderFromEnvTests(unittest.TestCase):

    def test_default_is_stub(self) -> None:
        p = build_provider_from_env({})
        self.assertIsInstance(p, StubTranscriptionProvider)

    def test_explicit_stub(self) -> None:
        p = build_provider_from_env({"JARVIS_STT_PROVIDER": "stub"})
        self.assertIsInstance(p, StubTranscriptionProvider)

    def test_faster_whisper_builds_with_env_config(self) -> None:
        p = build_provider_from_env({
            "JARVIS_STT_PROVIDER": "faster-whisper",
            "JARVIS_STT_MODEL": "small.en",
            "JARVIS_STT_COMPUTE": "int8",
            "JARVIS_STT_DEVICE": "cpu",
            "JARVIS_STT_LANGUAGE": "en",
            "JARVIS_FFMPEG": "C:/ffmpeg/bin/ffmpeg.exe",
        })
        self.assertIsInstance(p, FasterWhisperProvider)
        self.assertEqual(p._model_name, "small.en")
        self.assertEqual(p._compute_type, "int8")
        self.assertEqual(p._language, "en")
        self.assertEqual(p._ffmpeg_path, "C:/ffmpeg/bin/ffmpeg.exe")

    def test_whispercpp_builds(self) -> None:
        p = build_provider_from_env({
            "JARVIS_STT_PROVIDER": "whisper.cpp",
            "JARVIS_WHISPERCPP_BIN": "C:/tools/whisper-cli.exe",
            "JARVIS_STT_MODEL": "C:/models/ggml-base.en.bin",
        })
        self.assertIsInstance(p, WhisperCppProvider)
        self.assertEqual(p._binary, "C:/tools/whisper-cli.exe")
        self.assertEqual(p._model_path, "C:/models/ggml-base.en.bin")

    def test_auto_returns_composite_with_fallback_chain(self) -> None:
        p = build_provider_from_env({"JARVIS_STT_PROVIDER": "auto"})
        self.assertIsInstance(p, CompositeTranscriptionProvider)
        self.assertTrue(p.name.startswith("faster-whisper"))
        self.assertIn("stub", p.name)

    def test_empty_language_means_autodetect(self) -> None:
        p = build_provider_from_env({
            "JARVIS_STT_PROVIDER": "faster-whisper",
            "JARVIS_STT_LANGUAGE": "",
        })
        assert isinstance(p, FasterWhisperProvider)
        self.assertIsNone(p._language)

    def test_unknown_provider_raises_rather_than_silently_falling_back(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            build_provider_from_env({"JARVIS_STT_PROVIDER": "gpt-cloud-yolo"})
        self.assertIn("Unknown JARVIS_STT_PROVIDER", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
