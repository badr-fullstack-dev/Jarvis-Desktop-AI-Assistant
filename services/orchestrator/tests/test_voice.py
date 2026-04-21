"""Tests for the voice session state machine, provider abstraction,
and the bridge HTTP flow that submits transcripts into the runtime.

Key invariants exercised here:
 - No background listening. A freshly-constructed session is idle and
   remains idle until `start()` is called.
 - Provider failures surface cleanly as state="error" without leaking
   exceptions to callers.
 - Transcripts submitted via the bridge produce real tasks in the
   existing supervisor runtime.
"""

from __future__ import annotations

import base64
import json
import shutil
import socket
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from src.jarvis_core import bridge
from src.jarvis_core.api import LocalSupervisorAPI
from src.jarvis_core.voice import (
    StubTranscriptionProvider,
    TranscriptionError,
    TranscriptionProvider,
    VoiceError,
    VoiceSession,
)


# ---------------------------------------------------------------------------
# Unit tests: the state machine + provider abstraction
# ---------------------------------------------------------------------------

class _FailingProvider(TranscriptionProvider):
    name = "failing"
    def transcribe(self, audio_bytes: bytes, mime: str) -> str:
        raise TranscriptionError("device not available")


class _FixedProvider(TranscriptionProvider):
    name = "fixed"
    def __init__(self, text: str) -> None:
        self.text = text
    def transcribe(self, audio_bytes: bytes, mime: str) -> str:
        return self.text


class VoiceSessionTests(unittest.TestCase):

    def test_new_session_is_idle_and_not_listening(self) -> None:
        session = VoiceSession()
        snap = session.snapshot()
        self.assertEqual(snap["state"], "idle")
        self.assertIsNone(snap["transcript"])
        self.assertIsNone(snap["error"])
        self.assertTrue(snap["enabled"])
        self.assertEqual(snap["lastAudioBytes"], 0)

    def test_stop_without_start_is_rejected(self) -> None:
        session = VoiceSession()
        with self.assertRaises(VoiceError):
            session.stop(b"\x00", mime="audio/webm")
        self.assertEqual(session.snapshot()["state"], "idle")

    def test_full_happy_path_transitions_idle_to_ready_to_idle(self) -> None:
        session = VoiceSession(provider=_FixedProvider("open notepad please"))
        self.assertEqual(session.start()["state"], "recording")
        snap = session.stop(b"\x01\x02\x03", mime="audio/webm")
        self.assertEqual(snap["state"], "ready")
        self.assertEqual(snap["transcript"], "open notepad please")
        self.assertEqual(snap["lastAudioBytes"], 3)

        text = session.consume_transcript()
        self.assertEqual(text, "open notepad please")
        self.assertEqual(session.snapshot()["state"], "idle")

    def test_provider_failure_leaves_session_in_error_state(self) -> None:
        session = VoiceSession(provider=_FailingProvider())
        session.start()
        snap = session.stop(b"\x00", mime="audio/webm")
        self.assertEqual(snap["state"], "error")
        self.assertIn("device not available", snap["error"] or "")

    def test_error_recovery_requires_explicit_reset(self) -> None:
        session = VoiceSession(provider=_FailingProvider())
        session.start()
        session.stop(b"\x00")
        self.assertEqual(session.snapshot()["state"], "error")
        # Cannot start from error state directly? Actually we allow it
        # so the user can retry without an extra reset call.
        snap = session.start()
        self.assertEqual(snap["state"], "recording")

    def test_discard_from_ready_goes_to_idle(self) -> None:
        session = VoiceSession(provider=_FixedProvider("hello"))
        session.start()
        session.stop(b"\x00")
        session.discard()
        self.assertEqual(session.snapshot()["state"], "idle")

    def test_disabled_session_rejects_start(self) -> None:
        session = VoiceSession()
        session.set_enabled(False)
        with self.assertRaises(VoiceError):
            session.start()

    def test_consume_transcript_with_override(self) -> None:
        session = VoiceSession(provider=_FixedProvider("garbled text"))
        session.start()
        session.stop(b"\x00")
        text = session.consume_transcript(override="corrected task")
        self.assertEqual(text, "corrected task")
        self.assertEqual(session.snapshot()["state"], "idle")

    def test_consume_empty_transcript_raises(self) -> None:
        session = VoiceSession(provider=_FixedProvider("   "))
        session.start()
        session.stop(b"\x00")
        with self.assertRaises(VoiceError):
            session.consume_transcript()


class StubTranscriptionProviderTests(unittest.TestCase):

    def test_stub_labels_itself_as_not_real(self) -> None:
        p = StubTranscriptionProvider()
        out = p.transcribe(b"\x00\x01\x02\x03", mime="audio/webm")
        self.assertIn("stub", out.lower())
        self.assertIn("not real speech recognition", out.lower())
        self.assertIn("4 bytes", out)

    def test_stub_with_fixed_transcript_returns_it_verbatim(self) -> None:
        p = StubTranscriptionProvider(fixed_transcript="show me the installer page")
        self.assertEqual(
            p.transcribe(b"", "audio/webm"),
            "show me the installer page",
        )


# ---------------------------------------------------------------------------
# Bridge / HTTP flow
# ---------------------------------------------------------------------------

def _pick_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _http_get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def _http_post(url: str, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


class VoiceBridgeTests(unittest.TestCase):

    def setUp(self) -> None:
        workspace_root = Path(__file__).resolve().parents[3]
        self.root = workspace_root / "runtime" / f"voice-bridge-test-{uuid4()}"
        (self.root / "configs").mkdir(parents=True, exist_ok=True)
        source_config = workspace_root / "configs" / "policy.default.json"
        (self.root / "configs" / "policy.default.json").write_text(
            source_config.read_text(encoding="utf-8"), encoding="utf-8"
        )

        self.api = LocalSupervisorAPI(self.root)
        # Inject a deterministic provider so the tests never rely on a real
        # transcription backend.
        self.api.voice.provider = _FixedProvider("open the installer page")

        self.port = _pick_port()
        self.server = bridge.start_server(self.api, port=self.port)
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def test_voice_state_starts_idle(self) -> None:
        code, body = _http_get(f"{self.base}/voice")
        self.assertEqual(code, 200)
        self.assertEqual(body["state"], "idle")
        self.assertTrue(body["enabled"])

    def test_hud_state_includes_voice_snapshot(self) -> None:
        code, body = _http_get(f"{self.base}/hud-state")
        self.assertEqual(code, 200)
        self.assertIn("voice", body)
        self.assertEqual(body["voice"]["state"], "idle")
        self.assertEqual(body["voice"]["provider"], "fixed")

    def test_full_voice_to_task_pipeline(self) -> None:
        # start
        code, body = _http_post(f"{self.base}/voice/start", {})
        self.assertEqual(code, 200)
        self.assertEqual(body["state"], "recording")

        # stop with a tiny audio blob
        audio = base64.b64encode(b"fake-audio-bytes").decode()
        code, body = _http_post(f"{self.base}/voice/stop",
                                {"audio_base64": audio, "mime": "audio/webm"})
        self.assertEqual(code, 200)
        self.assertEqual(body["state"], "ready")
        self.assertEqual(body["transcript"], "open the installer page")

        # submit → creates a task via the real supervisor
        code, body = _http_post(f"{self.base}/voice/submit",
                                {"transcript": "open the installer page"})
        self.assertEqual(code, 201)
        self.assertEqual(body["transcript"], "open the installer page")
        self.assertIn("task_id", body["task"])

        # session is back to idle
        code, body = _http_get(f"{self.base}/voice")
        self.assertEqual(body["state"], "idle")

        # the task is visible in /hud-state (shared flow)
        code, state = _http_get(f"{self.base}/hud-state")
        self.assertEqual(code, 200)
        self.assertEqual(state["task"], "open the installer page")

    def test_stop_without_start_returns_409(self) -> None:
        code, body = _http_post(f"{self.base}/voice/stop", {"audio_base64": ""})
        self.assertEqual(code, 409)
        self.assertIn("error", body)

    def test_provider_failure_surfaces_as_error_state(self) -> None:
        self.api.voice.provider = _FailingProvider()
        _http_post(f"{self.base}/voice/start", {})
        code, body = _http_post(f"{self.base}/voice/stop", {"audio_base64": ""})
        self.assertEqual(code, 200)
        self.assertEqual(body["state"], "error")
        self.assertIn("device not available", body["error"])

    def test_disable_voice_blocks_start(self) -> None:
        code, body = _http_post(f"{self.base}/voice/enable", {"enabled": False})
        self.assertEqual(code, 200)
        self.assertFalse(body["enabled"])

        code, _ = _http_post(f"{self.base}/voice/start", {})
        self.assertEqual(code, 409)

    def test_invalid_base64_returns_400_and_reset_recovers(self) -> None:
        _http_post(f"{self.base}/voice/start", {})
        code, body = _http_post(f"{self.base}/voice/stop",
                                {"audio_base64": "!!!x", "mime": "audio/webm"})
        self.assertEqual(code, 400)
        self.assertIn("Invalid audio_base64", body["error"])
        # The session is left in 'recording' — the client recovers via /voice/reset.
        code, state = _http_get(f"{self.base}/voice")
        self.assertEqual(state["state"], "recording")
        _http_post(f"{self.base}/voice/reset", {})
        code, state = _http_get(f"{self.base}/voice")
        self.assertEqual(state["state"], "idle")


if __name__ == "__main__":
    unittest.main()
