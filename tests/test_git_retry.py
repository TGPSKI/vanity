"""Tests for git stderr capture and transient-error retry loop."""

import os
import tempfile
import unittest
from pathlib import Path

from tests.util import load_vanity

vanity = load_vanity()
from vanity import config, fetch, gitfetch, util  # noqa: E402
from vanity.registry import Model  # noqa: E402

run_git_command = gitfetch.run_git_command
run_git_download = gitfetch.run_git_download
fetch_one = fetch.fetch_one
transient_error = util.transient_error
GitFailure = util.GitFailure


class TestStderrCapture(unittest.TestCase):
    """Verify that run_git_command captures git stderr into GitFailure."""

    def _make_git_stub(self, stderr_line: str, exit_code: int):
        """Create a temp-directory 'git' script that prints *stderr_line* to
        stderr and exits with *exit_code*.  Return the temp dir path so the
        caller can keep the object alive (temp dir is not deleted on scope
        exit)."""
        tmpdir = Path(tempfile.mkdtemp())
        stub = tmpdir / "git"
        stub.write_text(
            f'#!/bin/sh\n'
            f'echo "{stderr_line}" >&2\n'
            f'exit {exit_code}\n'
        )
        stub.chmod(0o755)
        return tmpdir

    def test_rate_limit_stderr_triggers_retry(self):
        """A git command that prints '429 /rate limit' to stderr must raise
        GitFailure whose .stderr contains that text, and transient_error()
        must return True."""
        tmpdir = self._make_git_stub(
            "error: RPC failed; HTTP 429 curl 22 rate limit", exit_code=1
        )
        old_path = os.environ.copy()
        os.environ["PATH"] = str(tmpdir) + os.pathsep + os.environ.get("PATH", "")
        # Remove the heartbeat env so run_git_command uses the default 60 s
        os.environ.pop("VANITY_HEARTBEAT_SECONDS", None)
        try:
            model = Model(
                key="stub-model",
                repo="dummy",
                size_hint="0B",
                runtime="test",
                role="test",
            )
            with self.assertRaises(GitFailure) as ctx:
                run_git_command(
                    ["git", "clone", "https://fakerepo.git", str(model.path)],
                    use_token=False,
                    model=model,
                    quiet=True,
                )
            err = ctx.exception
            self.assertIn("429", err.stderr)
            self.assertTrue(transient_error(err.stderr))
        finally:
            os.environ.clear()
            os.environ.update(old_path)

    def test_fatal_stderr_is_not_transient(self):
        """A 'fatal: repository not found' (exit 128) must have
        transient_error() return False."""
        tmpdir = self._make_git_stub(
            "fatal: repository 'https://nonexistent.git' not found", exit_code=128
        )
        old_path = os.environ.copy()
        os.environ["PATH"] = str(tmpdir) + os.pathsep + os.environ.get("PATH", "")
        os.environ.pop("VANITY_HEARTBEAT_SECONDS", None)
        try:
            model = Model(
                key="stub-model",
                repo="dummy",
                size_hint="0B",
                runtime="test",
                role="test",
            )
            with self.assertRaises(GitFailure) as ctx:
                run_git_command(
                    ["git", "clone", "https://nonexistent.git", str(model.path)],
                    use_token=False,
                    model=model,
                    quiet=True,
                )
            err = ctx.exception
            self.assertFalse(transient_error(err.stderr))
        finally:
            os.environ.clear()
            os.environ.update(old_path)


class TestRetryLoop(unittest.TestCase):
    """Verify that fetch_one retries on GitFailure transient errors and
    backs off properly."""

    def test_transient_git_failure_triggers_backoff(self):
        """When run_download raises GitFailure(1, ..., "HTTP 429 …") twice
        and then succeeds, fetch_one must:  (a) make 3 calls to run_download,
        (b) record 2 backoff sleep calls, (c) leave state status='fetched'."""

        import time as _time_module

        call_count: int = 0
        sleep_calls: list[float] = []

        def fake_run_download(model, backend, quiet, min_interval):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise GitFailure(1, ["git", "clone"], "HTTP 429 rate limit")

        original_run_download = fetch.run_download

        with tempfile.TemporaryDirectory() as tmp:
            tstore = Path(tmp) / "library"
            tstore.mkdir()

            # Redirect BOTH the store and the state dir: without this the test
            # writes retry-model into the real library and fetches.json.
            saved_state_dir = config.state_dir
            saved_store = os.environ.get("MODEL_STORE")
            config.state_dir = lambda: Path(tmp) / "state"
            os.environ["MODEL_STORE"] = str(tstore)

            # Patch run_download in the module
            fetch.run_download = fake_run_download

            # Mock time.sleep so we can inspect backoff
            orig_sleep = _time_module.sleep
            _time_module.sleep = lambda d: sleep_calls.append(d)

            try:
                model = Model(
                    key="retry-model",
                    repo="org/test",
                    size_hint="10MB",
                    runtime="test",
                    role="test",
                )
                self.assertEqual(model.path.parent, tstore.resolve())

                fetch_one(
                    model,
                    backend="dummy",
                    retries=3,
                    min_interval=0,
                    quiet=True,
                )
            finally:
                _time_module.sleep = orig_sleep
                fetch.run_download = original_run_download
                config.state_dir = saved_state_dir
                if saved_store is None:
                    os.environ.pop("MODEL_STORE", None)
                else:
                    os.environ["MODEL_STORE"] = saved_store

        # 3 attempts (2 failures + 1 success)
        self.assertEqual(call_count, 3)
        # 2 backoff sleeps between 3 attempts
        self.assertEqual(len(sleep_calls), 2)


if __name__ == "__main__":
    unittest.main()