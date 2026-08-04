"""Tests for shared helpers extracted during the audit.

display_path exists because the http backend used Path.relative_to(ROOT) for
log lines, which raises ValueError as soon as the library lives outside the
checkout -- the normal case once a store is configured.

Run: python3 -m unittest discover -s tests -p 'test_util_helpers*'
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.util import load_vanity

vanity = load_vanity()
from vanity import config, util  # noqa: E402


class TestDisplayPath(unittest.TestCase):
    def test_relative_when_under_the_store(self) -> None:
        store = Path("/srv/models")
        with mock.patch.object(config, "store_root", return_value=store):
            self.assertEqual(
                util.display_path(store / "qwq-32b-awq" / "config.json"),
                "qwq-32b-awq/config.json",
            )

    def test_absolute_when_outside_the_store(self) -> None:
        """The regression: this input used to raise ValueError mid-download."""
        with mock.patch.object(config, "store_root", return_value=Path("/srv/models")):
            outside = Path("/mnt/elsewhere/model/config.json")
            self.assertEqual(util.display_path(outside), str(outside))


class TestPidAlive(unittest.TestCase):
    def test_current_process_is_alive(self) -> None:
        self.assertTrue(util.pid_alive(os.getpid()))

    def test_reaped_process_is_dead(self) -> None:
        proc = subprocess.Popen([sys.executable, "-c", ""])
        proc.wait()
        self.assertFalse(util.pid_alive(proc.pid))

    def test_nonsense_pids(self) -> None:
        for pid in (0, -1):
            with self.subTest(pid=pid):
                self.assertFalse(util.pid_alive(pid))


class TestCommandOk(unittest.TestCase):
    def test_zero_exit(self) -> None:
        self.assertTrue(util.command_ok([sys.executable, "-c", ""]))

    def test_nonzero_exit(self) -> None:
        self.assertFalse(util.command_ok([sys.executable, "-c", "raise SystemExit(3)"]))

    def test_missing_binary_is_not_an_error(self) -> None:
        self.assertFalse(util.command_ok(["definitely-not-a-real-binary-xyz"]))

    def test_any_command_ok(self) -> None:
        self.assertTrue(util.any_command_ok([
            ["definitely-not-a-real-binary-xyz"],
            [sys.executable, "-c", ""],
        ]))
        self.assertFalse(util.any_command_ok([["definitely-not-a-real-binary-xyz"]]))


class TestHumanSize(unittest.TestCase):
    def test_units(self) -> None:
        self.assertEqual(util.human_size(0), "0B")
        self.assertEqual(util.human_size(512), "512B")
        self.assertEqual(util.human_size(1024), "1.0KB")
        self.assertEqual(util.human_size(1024 ** 3), "1.0GB")


class TestHumanDuration(unittest.TestCase):
    def test_formats(self) -> None:
        self.assertEqual(util.human_duration(5), "5s")
        self.assertEqual(util.human_duration(65), "1m05s")
        self.assertEqual(util.human_duration(3725), "1h02m05s")


class TestBytesOnDisk(unittest.TestCase):
    def test_counts_nested_files_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sub").mkdir()
            (root / "a.bin").write_bytes(b"x" * 10)
            (root / "sub" / "b.bin").write_bytes(b"y" * 5)
            self.assertEqual(util.bytes_on_disk(root), 15)

    def test_missing_path_is_zero(self) -> None:
        self.assertEqual(util.bytes_on_disk(Path("/nope/does/not/exist")), 0)


if __name__ == "__main__":
    unittest.main()
