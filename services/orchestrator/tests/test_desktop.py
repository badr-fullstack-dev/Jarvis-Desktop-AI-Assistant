"""Tests for the Windows desktop capability adapter and app.focus.

Real win32 calls are never made in these tests: the adapter accepts
injection hooks for clipboard read/write, notification, foreground
window lookup, window enumeration, and the SetForegroundWindow call.
We exercise the guarded path via LocalSupervisorAPI so the gateway and
policy engine are in the picture too.
"""

from __future__ import annotations

import asyncio
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from src.jarvis_core.api import LocalSupervisorAPI
from src.jarvis_core.capabilities.applications import ApplicationCapability
from src.jarvis_core.capabilities.desktop import DesktopCapability
from src.jarvis_core.models import ActionProposal


class _DesktopTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace_root = Path(__file__).resolve().parents[3]
        self.root = self.workspace_root / "runtime" / f"desktop-test-{uuid4()}"
        (self.root / "configs").mkdir(parents=True, exist_ok=True)
        (self.root / "configs" / "policy.default.json").write_text(
            (self.workspace_root / "configs" / "policy.default.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.api = LocalSupervisorAPI(self.root)
        self.task = asyncio.run(self.api.submit_voice_or_text_task("desktop test bed"))

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def _prop(self, capability: str, parameters: dict, *,
              confidence: float = 0.95, dry_run: bool = False) -> ActionProposal:
        return ActionProposal(
            task_id=self.task.task_id,
            capability=capability,
            intent="desktop test",
            parameters=parameters,
            requested_by="test",
            evidence=["unit"],
            confidence=confidence,
            dry_run=dry_run,
        )


class DesktopClipboardTests(unittest.TestCase):
    def test_write_then_read_round_trip_in_memory(self) -> None:
        buf = {"text": ""}
        adapter = DesktopCapability(
            clipboard_read_fn=lambda: buf["text"],
            clipboard_write_fn=lambda t: buf.__setitem__("text", t),
            platform="win32",
        )
        write = adapter.execute(ActionProposal(
            task_id="t", capability="desktop.clipboard_write",
            intent="i", parameters={"text": "hello jarvis"},
            requested_by="test", evidence=[],
        ))
        self.assertEqual(write.status, "executed")
        self.assertEqual(buf["text"], "hello jarvis")

        read = adapter.execute(ActionProposal(
            task_id="t", capability="desktop.clipboard_read",
            intent="i", parameters={},
            requested_by="test", evidence=[],
        ))
        self.assertEqual(read.status, "executed")
        self.assertEqual(read.output["text"], "hello jarvis")
        self.assertFalse(read.output["truncated"])

    def test_read_truncates_oversized_clipboard(self) -> None:
        big = "x" * 10000
        adapter = DesktopCapability(
            clipboard_read_fn=lambda: big,
            platform="win32",
        )
        result = adapter.execute(ActionProposal(
            task_id="t", capability="desktop.clipboard_read",
            intent="i", parameters={}, requested_by="test", evidence=[],
        ))
        self.assertEqual(result.status, "executed")
        self.assertTrue(result.output["truncated"])
        self.assertLess(len(result.output["text"]), len(big))
        self.assertEqual(result.output["byte_count"], len(big))

    def test_write_rejects_non_string(self) -> None:
        adapter = DesktopCapability(platform="win32")
        result = adapter.execute(ActionProposal(
            task_id="t", capability="desktop.clipboard_write",
            intent="i", parameters={"text": 123},
            requested_by="test", evidence=[],
        ))
        self.assertEqual(result.status, "failed")
        self.assertIn("required", result.output["error"].lower())

    def test_write_rejects_oversized_payload(self) -> None:
        adapter = DesktopCapability(
            clipboard_write_fn=lambda t: None,
            platform="win32",
        )
        result = adapter.execute(ActionProposal(
            task_id="t", capability="desktop.clipboard_write",
            intent="i", parameters={"text": "y" * (64 * 1024 + 1)},
            requested_by="test", evidence=[],
        ))
        self.assertEqual(result.status, "failed")
        self.assertIn("too large", result.output["error"])


class DesktopPlatformGuardTests(unittest.TestCase):
    def test_non_windows_fails_honestly(self) -> None:
        adapter = DesktopCapability(platform="linux")
        result = adapter.execute(ActionProposal(
            task_id="t", capability="desktop.clipboard_read",
            intent="i", parameters={}, requested_by="test", evidence=[],
        ))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.output["error"], "platform_unsupported")


class DesktopNotifyTests(unittest.TestCase):
    def test_notify_dispatches_once(self) -> None:
        calls = []
        adapter = DesktopCapability(
            notify_fn=lambda t, m: calls.append((t, m)),
            platform="win32",
        )
        result = adapter.execute(ActionProposal(
            task_id="t", capability="desktop.notify",
            intent="i", parameters={"title": "Hi", "message": "hello"},
            requested_by="test", evidence=[],
        ))
        self.assertEqual(result.status, "executed")
        self.assertEqual(calls, [("Hi", "hello")])
        self.assertEqual(result.output["channel"], "dialog")

    def test_notify_dry_run_does_not_call(self) -> None:
        calls = []
        adapter = DesktopCapability(
            notify_fn=lambda t, m: calls.append((t, m)),
            platform="win32",
        )
        result = adapter.execute(ActionProposal(
            task_id="t", capability="desktop.notify",
            intent="i", parameters={"message": "dry"},
            requested_by="test", evidence=[], dry_run=True,
        ))
        self.assertEqual(result.status, "executed")
        self.assertTrue(result.output["dry_run"])
        self.assertEqual(calls, [])

    def test_notify_requires_message(self) -> None:
        adapter = DesktopCapability(platform="win32")
        result = adapter.execute(ActionProposal(
            task_id="t", capability="desktop.notify",
            intent="i", parameters={"title": "only"},
            requested_by="test", evidence=[],
        ))
        self.assertEqual(result.status, "failed")


class DesktopForegroundTests(unittest.TestCase):
    def test_foreground_window_returns_injected_info(self) -> None:
        adapter = DesktopCapability(
            foreground_window_fn=lambda: {
                "hwnd": 42, "title": "Untitled - Notepad",
                "pid": 1234, "exe": r"C:\\Windows\\System32\\notepad.exe",
            },
            platform="win32",
        )
        result = adapter.execute(ActionProposal(
            task_id="t", capability="desktop.foreground_window",
            intent="i", parameters={}, requested_by="test", evidence=[],
        ))
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.output["window"]["pid"], 1234)
        self.assertIn("Notepad", result.summary)


class AppFocusTests(_DesktopTestBase):
    def test_focus_rejects_unknown_app(self) -> None:
        prop = self._prop("app.focus", {"name": "totally-fake"}, confidence=0.99)
        result = self.api.submit_action(prop, approved=False)
        self.assertEqual(result.status, "failed")
        self.assertIn("allowlist", result.output["error"].lower())

    def test_focus_when_not_running_fails_with_not_running(self) -> None:
        # Swap in a fake ApplicationCapability that reports zero matches.
        fake = ApplicationCapability(
            find_hwnds_fn=lambda _exe: [],
            focus_hwnd_fn=lambda _hwnd: False,
            platform="win32",
        )
        prop = ActionProposal(
            task_id=self.task.task_id, capability="app.focus",
            intent="focus notepad", parameters={"name": "notepad"},
            requested_by="test", evidence=[], confidence=0.99,
        )
        result = fake.execute(prop)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.output["error"], "not_running")

    def test_focus_succeeds_when_window_found(self) -> None:
        fake = ApplicationCapability(
            find_hwnds_fn=lambda _exe: [(0x1234, 999, _exe)],
            focus_hwnd_fn=lambda _hwnd: True,
            platform="win32",
        )
        prop = ActionProposal(
            task_id=self.task.task_id, capability="app.focus",
            intent="focus notepad", parameters={"name": "notepad"},
            requested_by="test", evidence=[], confidence=0.99,
        )
        result = fake.execute(prop)
        self.assertEqual(result.status, "executed")
        self.assertTrue(result.output["focused"])
        self.assertEqual(result.output["hwnd"], 0x1234)

    def test_focus_honest_failure_when_set_foreground_refused(self) -> None:
        fake = ApplicationCapability(
            find_hwnds_fn=lambda _exe: [(0xABCD, 111, _exe)],
            focus_hwnd_fn=lambda _hwnd: False,
            platform="win32",
        )
        prop = ActionProposal(
            task_id=self.task.task_id, capability="app.focus",
            intent="focus notepad", parameters={"name": "notepad"},
            requested_by="test", evidence=[], confidence=0.99,
        )
        result = fake.execute(prop)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.output["error"], "set_foreground_refused")

    def test_focus_non_windows_platform_fails_honestly(self) -> None:
        fake = ApplicationCapability(platform="linux")
        prop = ActionProposal(
            task_id=self.task.task_id, capability="app.focus",
            intent="focus notepad", parameters={"name": "notepad"},
            requested_by="test", evidence=[], confidence=0.99,
        )
        result = fake.execute(prop)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.output["error"], "platform_unsupported")


class DesktopGuardedPathTests(_DesktopTestBase):
    """Exercise the full ActionGateway/PolicyEngine route for desktop caps."""

    def _install_fake_desktop(self, *, buf: dict, notify_log: list,
                              foreground_info: dict) -> None:
        fake = DesktopCapability(
            clipboard_read_fn=lambda: buf["text"],
            clipboard_write_fn=lambda t: buf.__setitem__("text", t),
            notify_fn=lambda t, m: notify_log.append((t, m)),
            foreground_window_fn=lambda: foreground_info,
            platform="win32",
        )
        adapters = self.api.gateway.adapters
        for i, a in enumerate(adapters):
            if isinstance(a, DesktopCapability):
                adapters[i] = fake
                return
        adapters.append(fake)

    def test_clipboard_read_runs_through_gateway_tier0(self) -> None:
        buf = {"text": "prefilled"}
        self._install_fake_desktop(buf=buf, notify_log=[], foreground_info={})
        prop = self._prop("desktop.clipboard_read", {})
        result = self.api.submit_action(prop, approved=False)
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.output["text"], "prefilled")

    def test_clipboard_write_is_tier1_and_runs_when_confident(self) -> None:
        buf = {"text": ""}
        self._install_fake_desktop(buf=buf, notify_log=[], foreground_info={})
        prop = self._prop("desktop.clipboard_write", {"text": "hello"},
                          confidence=0.99)
        result = self.api.submit_action(prop, approved=False)
        self.assertEqual(result.status, "executed")
        self.assertEqual(buf["text"], "hello")

    def test_notify_is_tier1_executes_via_gateway(self) -> None:
        notify_log: list = []
        self._install_fake_desktop(buf={"text": ""}, notify_log=notify_log,
                                   foreground_info={})
        prop = self._prop("desktop.notify",
                          {"title": "T", "message": "m"}, confidence=0.99)
        result = self.api.submit_action(prop, approved=False)
        self.assertEqual(result.status, "executed")
        self.assertEqual(notify_log, [("T", "m")])

    def test_foreground_window_tier0_executes(self) -> None:
        info = {"hwnd": 7, "title": "x", "pid": 10, "exe": "z.exe"}
        self._install_fake_desktop(buf={"text": ""}, notify_log=[],
                                   foreground_info=info)
        prop = self._prop("desktop.foreground_window", {})
        result = self.api.submit_action(prop, approved=False)
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.output["window"]["title"], "x")


if __name__ == "__main__":
    unittest.main()
