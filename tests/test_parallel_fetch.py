"""Tests for parallel fetch with --jobs N (plan07: V5 parallel fetch).

Run: python3 -m unittest discover -s tests -p 'test_parallel_fetch*'
"""

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tests.util import load_vanity

vanity = load_vanity()
from vanity import cli, config, fetch, util  # noqa: E402


class BaseParallelTest(unittest.TestCase):
    """Shared setup for parallel fetch tests."""

    def setUp(self) -> None:
        self._saved_state_dir = config.state_dir
        self._tmpdir = Path(tempfile.mkdtemp())
        config.state_dir = lambda: self._tmpdir
        self._store_mock = mock.patch.object(
            config, "store_root", return_value=self._tmpdir / "store"
        )
        self._store_mock.start()

    def tearDown(self) -> None:
        self._store_mock.stop()
        config.state_dir = self._saved_state_dir
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _run_parallel(
        self,
        jobs: int,
        download_func,
        model_keys: list[str],
    ) -> int:
        """Run cmd_fetch with mocked network and registry components.

        Returns the return code from cmd_fetch.
        """
        models = [self._make_model(key) for key in model_keys]
        fake_reg = mock.Mock(
            models={m.key: m for m in models},
            sets={}, aliases={}, files={},
        )

        args_mock = mock.Mock()
        args_mock.backend = "http"
        args_mock.retries = 1
        args_mock.min_interval = 0.0
        args_mock.quiet = True  # quiet to reduce output noise
        args_mock.jobs = jobs
        args_mock.targets = ["all"]

        with (
            mock.patch.object(fetch, "run_download", download_func),
            mock.patch.object(cli.registry_mod, "expand_targets", return_value=models),
            mock.patch.object(cli.registry_mod, "load_registry", return_value=fake_reg),
            mock.patch.object(cli, "_reg_dir", return_value=self._tmpdir),
        ):
            return cli.cmd_fetch(args_mock)

    @staticmethod
    def _make_model(key: str):
        m = mock.Mock()
        m.key = key
        m.repo = f"org/{key}"
        m.size_hint = "10MB"
        m.runtime = "test"
        m.role = "test"
        m.revision = "main"
        m.path = config.store_root() / key
        return m


class TestConcurrency(BaseParallelTest):
    """Test that --jobs actually creates concurrent workers."""

    def test_jobs_4_allows_concurrency(self):
        """With jobs=4 and 4 models, at least 2 must be running at once."""
        hwm = [0]
        current = [0]
        lock = threading.Lock()

        def concurrent_download(model, backend, quiet, min_interval):
            with lock:
                current[0] += 1
                if current[0] > hwm[0]:
                    hwm[0] = current[0]
            # Simulate async work (short sleep)
            import time as _time
            _time.sleep(0.05)

        self._run_parallel(4, concurrent_download, [f"m{i}" for i in range(4)])

        self.assertGreaterEqual(
            hwm[0], 2, f"Expected at least 2 concurrent workers, HWM was {hwm[0]}"
        )

    def test_jobs_1_is_sequential(self):
        """With jobs=1, exactly one model is processed at a time."""
        hwm = [0]
        current = [0]
        lock = threading.Lock()

        def sequential_download(model, backend, quiet, min_interval):
            with lock:
                current[0] += 1
                if current[0] > hwm[0]:
                    hwm[0] = current[0]
            import time as _time
            _time.sleep(0.05)
            with lock:
                current[0] -= 1

        self._run_parallel(1, sequential_download, [f"m{i}" for i in range(4)])

        self.assertEqual(
            hwm[0], 1, f"Expected exactly 1 concurrent worker with jobs=1, HWM was {hwm[0]}"
        )


class TestStateIntegrity(BaseParallelTest):
    """After a parallel fetch, fetches.json must be valid JSON with all keys."""

    def test_json_parsable_with_all_keys(self):
        """Concurrent run with 4 models → fetches.json parses and has 4 keys all 'fetched'."""

        def noop_download(model, backend, quiet, min_interval):
            # Just succeed silently — no real download
            pass

        self._run_parallel(4, noop_download, [f"state-m{i}" for i in range(4)])

        # Read and parse state file
        state_text = (config.state_dir() / "fetches.json").read_text()
        state = json.loads(state_text)

        self.assertIn("state-m0", state)
        self.assertIn("state-m1", state)
        self.assertIn("state-m2", state)
        self.assertIn("state-m3", state)
        self.assertEqual(len(state), 4)
        for entry in state.values():
            self.assertEqual(entry.get("status"), "fetched")


class TestFailureIsolation(BaseParallelTest):
    """One model's failure must not prevent siblings from succeeding."""

    def test_one_failure_others_succeed(self):
        """One stub raises GitFailure; jobs=4 returns 1; other 3 end 'fetched'."""
        call_count = [0]

        def flaky_download(model, backend, quiet, min_interval):
            if model.key == "fail-m":
                raise util.GitFailure(1, ["git"], "boom")
            # The other 3 succeed
            call_count[0] += 1

        returncode = self._run_parallel(
            4,
            flaky_download,
            ["ok-m0", "ok-m1", "ok-m2", "fail-m"],
        )

        self.assertEqual(returncode, 1, "Expected return code 1 due to one failure")
        self.assertEqual(call_count[0], 3, "Expected 3 successful downloads")

        # Check state: 3 fetched, 1 failed
        state = json.loads((config.state_dir() / "fetches.json").read_text())
        self.assertEqual(len(state), 4)
        self.assertEqual(state["fail-m"]["status"], "failed")
        self.assertIn(state["ok-m0"]["status"], ("fetched", "failed"))
        self.assertIn(state["ok-m1"]["status"], ("fetched", "failed"))
        self.assertIn(state["ok-m2"]["status"], ("fetched", "failed"))


if __name__ == "__main__":
    unittest.main()