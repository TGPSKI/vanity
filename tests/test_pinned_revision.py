"""Tests for pinned commit-SHA revisions in the git backends.

`git clone --branch` accepts branch and tag names only -- given a raw commit
SHA it fails with "Remote branch <sha> not found in upstream origin". A pinned
revision therefore has to clone first and detach onto the commit afterwards.

These tests stub `git` on PATH and assert on the argv it receives, so they
cover the command construction without touching the network.

Run: python3 -m unittest discover -s tests -p 'test_pinned_revision*'
"""

import os
import tempfile
import unittest
from pathlib import Path

from tests.util import load_vanity

vanity = load_vanity()
from vanity import config, gitfetch  # noqa: E402
from vanity.registry import Model  # noqa: E402

PIN = "71034c5d8bde858ff824298bdedc65515b97d2b9"


class TestIsCommitSha(unittest.TestCase):
    def test_full_and_abbreviated_shas(self) -> None:
        self.assertTrue(gitfetch.is_commit_sha(PIN))
        self.assertTrue(gitfetch.is_commit_sha("71034c5"))

    def test_branch_and_tag_names(self) -> None:
        for name in ("main", "refs/heads/main", "v1.0", "", "MAIN", "71034c"):
            with self.subTest(name=name):
                self.assertFalse(gitfetch.is_commit_sha(name))


class TestPinnedCloneCommands(unittest.TestCase):
    """Assert the argv git actually receives for pinned vs unpinned revisions."""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp())
        self._log = self._tmp / "argv.log"

        stub_dir = self._tmp / "bin"
        stub_dir.mkdir()
        stub = stub_dir / "git"
        stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{self._log}"\nexit 0\n')
        stub.chmod(0o755)

        self._saved_env = os.environ.copy()
        os.environ["PATH"] = str(stub_dir) + os.pathsep + os.environ.get("PATH", "")
        # Isolate the store so model.path points somewhere that does not exist.
        os.environ["MODEL_STORE"] = str(self._tmp / "store")

        self._saved_dotenv = config.DOTENV
        config.DOTENV = self._tmp / "absent.env"

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved_env)
        config.DOTENV = self._saved_dotenv

    def _run(self, revision: str) -> list[str]:
        model = Model(
            key="stub-model",
            repo="org/Repo",
            size_hint="0B",
            runtime="test",
            role="test",
            revision=revision,
        )
        gitfetch.run_git_download(model, backend="git-lfs", quiet=True)
        lines = self._log.read_text().splitlines()
        # Drop the `git lfs version` availability probe.
        return [ln for ln in lines if not ln.startswith("lfs ")]

    def test_pinned_sha_clones_then_detaches(self) -> None:
        calls = self._run(PIN)

        clone = next(c for c in calls if c.startswith("clone"))
        self.assertNotIn(
            "--branch",
            clone,
            "a commit SHA must not be passed to `git clone --branch`",
        )

        self.assertTrue(
            any(f"checkout --detach {PIN}" in c for c in calls),
            f"expected a detaching checkout onto the pin; got {calls}",
        )

    def test_unpinned_revision_still_uses_branch(self) -> None:
        calls = self._run("main")

        clone = next(c for c in calls if c.startswith("clone"))
        self.assertIn("--branch main", clone)

        self.assertFalse(
            any("checkout --detach" in c for c in calls),
            "an unpinned revision must not detach HEAD",
        )


if __name__ == "__main__":
    unittest.main()
