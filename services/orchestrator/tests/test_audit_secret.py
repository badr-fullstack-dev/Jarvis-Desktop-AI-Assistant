"""Pre-public-release security tests for the local audit signing secret.

These tests exist to make it impossible to silently re-introduce a
hard-coded shared secret for ``SignedEventLog``.

Acceptance criteria covered here:
 - The string ``jarvis-local-dev-secret`` is not present anywhere in the
   shipped Python package source.
 - Two separate runtime directories produce two different generated
   audit keys (i.e. each install has its own).
 - ``JARVIS_AUDIT_SECRET`` overrides file-based generation.
 - The generated ``audit.key`` is reused on subsequent constructions.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from src.jarvis_core.api import (
    LocalSupervisorAPI,
    _AUDIT_KEY_FILENAME,
    _load_or_create_audit_secret,
)


_REPO_ROOT = Path(__file__).resolve().parents[3]
_PACKAGE_ROOT = _REPO_ROOT / "services" / "orchestrator" / "src" / "jarvis_core"


class HardcodedSecretAbsent(unittest.TestCase):
    """Defence in depth: scan shipped sources for the old dev secret."""

    BANNED_TOKEN = "jarvis-local-dev-secret"

    def test_token_not_in_package_sources(self) -> None:
        self.assertTrue(_PACKAGE_ROOT.is_dir(), _PACKAGE_ROOT)
        offenders = []
        for path in _PACKAGE_ROOT.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if self.BANNED_TOKEN in text:
                offenders.append(str(path))
        self.assertEqual(
            offenders,
            [],
            f"hard-coded dev secret found in: {offenders}",
        )


class AuditSecretIsolation(unittest.TestCase):
    """Each runtime dir must get its own random key."""

    def setUp(self) -> None:
        # Make sure no env override leaks in from the developer's shell.
        self._saved = os.environ.pop("JARVIS_AUDIT_SECRET", None)
        self.tmp = Path(tempfile.mkdtemp(prefix="jarvis-audit-"))

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ["JARVIS_AUDIT_SECRET"] = self._saved
        else:
            os.environ.pop("JARVIS_AUDIT_SECRET", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_two_runtime_dirs_get_different_secrets(self) -> None:
        a = self.tmp / "runtime_a"
        b = self.tmp / "runtime_b"
        secret_a = _load_or_create_audit_secret(a)
        secret_b = _load_or_create_audit_secret(b)
        self.assertTrue(secret_a)
        self.assertTrue(secret_b)
        self.assertNotEqual(
            secret_a,
            secret_b,
            "each runtime dir must generate its own audit key",
        )
        self.assertTrue((a / _AUDIT_KEY_FILENAME).exists())
        self.assertTrue((b / _AUDIT_KEY_FILENAME).exists())

    def test_secret_is_not_the_old_hardcoded_value(self) -> None:
        secret = _load_or_create_audit_secret(self.tmp / "runtime_c")
        self.assertNotEqual(secret, "jarvis-local-dev-secret")
        # token_hex(32) → 64 hex chars
        self.assertGreaterEqual(len(secret), 32)

    def test_existing_key_is_reused(self) -> None:
        runtime = self.tmp / "runtime_reuse"
        first = _load_or_create_audit_secret(runtime)
        second = _load_or_create_audit_secret(runtime)
        self.assertEqual(first, second)

    def test_env_var_overrides_file(self) -> None:
        runtime = self.tmp / "runtime_env"
        os.environ["JARVIS_AUDIT_SECRET"] = "env-supplied-secret"
        try:
            secret = _load_or_create_audit_secret(runtime)
        finally:
            os.environ.pop("JARVIS_AUDIT_SECRET", None)
        self.assertEqual(secret, "env-supplied-secret")
        # Env path must not write a file to disk.
        self.assertFalse((runtime / _AUDIT_KEY_FILENAME).exists())

    def test_local_supervisor_api_uses_per_root_secret(self) -> None:
        # Two LocalSupervisorAPI instances pointed at different repo roots
        # must produce different audit secrets.
        root_a = self.tmp / "repo_a"
        root_b = self.tmp / "repo_b"
        real_policy = (
            _REPO_ROOT / "configs" / "policy.default.json"
        ).read_text(encoding="utf-8")
        for root in (root_a, root_b):
            (root / "configs").mkdir(parents=True)
            (root / "configs" / "policy.default.json").write_text(
                real_policy, encoding="utf-8"
            )
        api_a = LocalSupervisorAPI(root_a)
        api_b = LocalSupervisorAPI(root_b)
        self.assertNotEqual(api_a.event_log.secret, api_b.event_log.secret)
        self.assertNotEqual(api_a.event_log.secret, b"jarvis-local-dev-secret")


if __name__ == "__main__":
    unittest.main()
