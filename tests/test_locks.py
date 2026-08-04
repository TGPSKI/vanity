"""Pinning tests for the stale-lock-break feature (plan05: V4 PID-liveness stale-lock break).

These tests verify that acquire_lock properly handles:
1. Dead-PID locks (stale) → should be broken and the caller gets the lock
2. Live-PID locks → should be refused
3. Garbage lock content → should be refused

Run: python3 -m unittest discover -s tests -p 'test_locks*'
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.util import load_vanity

vanity = load_vanity()
from vanity import config, registry, state, util  # noqa: E402
from vanity.util import pid_alive as _pid_alive  # noqa: E402


class TestAcquireLockStalePid(unittest.TestCase):
    """Tests for stale-lock-break in acquire_lock.

    We point config.state_dir() at a temporary directory.
    """

    def setUp(self) -> None:
        self._saved_state_dir = config.state_dir
        self._tmpdir = Path(tempfile.mkdtemp())
        config.state_dir = lambda: self._tmpdir

    def tearDown(self) -> None:
        config.state_dir = self._saved_state_dir
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _lock_path(self, key: str) -> Path:
        return self._tmpdir / f"{key}.lock"

    def _write_lock(self, key: str, content: str) -> Path:
        path = self._lock_path(key)
        path.write_text(content)
        return path

    def _model(self, key: str) -> "registry.Model":
        """Build a synthetic Model.

        Lock semantics depend only on the key, so these tests must not load the
        real registry — otherwise editing registry/ breaks the suite.
        """
        return registry.Model(
            key=key,
            repo="example/Repo",
            size_hint="~1GB",
            runtime="test",
            role="test",
        )


class TestDeadPidLock(TestAcquireLockStalePid):
    """Pin: dead-PID lock should be broken; acquire_lock succeeds."""

    def test_dead_pid_breaks_stale_lock(self):
        # Spawn a short-lived process and wait for it to die
        proc = subprocess.Popen(["true"])
        pid = proc.pid
        proc.wait()

        self.assertFalse(_pid_alive(pid), "test setup failed: PID should be dead")

        key = "test-model-dead-pid"
        content = f"{pid} {util.now()}\n"
        self._write_lock(key, content)

        model = self._model(key)

        # BEFORE FIX: SystemExit because acquire_lock refuses all existing locks.
        # AFTER FIX: stale lock is broken, our PID written.
        state.acquire_lock(model)

        lock_path = self._lock_path(key)
        self.assertTrue(lock_path.exists())
        actual = lock_path.read_text().strip()
        self.assertTrue(actual.startswith(str(os.getpid())))


class TestLivePidLock(TestAcquireLockStalePid):
    """Live-PID lock should be refused, file untouched."""

    def test_live_pid_refused(self):
        key = "test-model-live-pid"
        content = f"{os.getpid()} {util.now()}\n"
        lock_path = self._write_lock(key, content)

        model = self._model(key)

        with self.assertRaises(vanity.FetchError):
            state.acquire_lock(model)

        # Lock file should be untouched
        self.assertEqual(lock_path.read_text(), content)


class TestGarbageLock(TestAcquireLockStalePid):
    """Garbage lock content should be refused, file untouched."""

    def test_garbage_content_refused(self):
        key = "test-model-garbage"
        content = "not-a-pid\n"
        lock_path = self._write_lock(key, content)

        model = self._model(key)

        with self.assertRaises(vanity.FetchError):
            state.acquire_lock(model)

        # Lock file should be untouched
        self.assertEqual(lock_path.read_text(), content)


if __name__ == "__main__":
    unittest.main()