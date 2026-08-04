"""Tests for store/registry resolution and the first-run prompt.

An installed `vanity` can be invoked from anywhere, so config.ROOT (the source
checkout the package was imported from) is only a last-resort default. These
tests pin the resolution order and, importantly, that the first-run prompt
never fires when stdin is not a terminal -- otherwise scripts and CI would hang
waiting on input.

Run: python3 -m unittest discover -s tests -p 'test_config_resolution*'
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.util import load_vanity

vanity = load_vanity()
from vanity import cli, config  # noqa: E402


class ConfigTestCase(unittest.TestCase):
    """Redirect XDG_CONFIG_HOME so the real user config is never touched."""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp())
        self._saved_env = os.environ.copy()
        os.environ["XDG_CONFIG_HOME"] = str(self._tmp / "xdg")
        for name in ("MODEL_STORE", "MODELS_DIR", "VANITY_REGISTRY_DIR"):
            os.environ.pop(name, None)
        # .env in the checkout must not leak into resolution under test.
        self._saved_dotenv = config.DOTENV
        config.DOTENV = self._tmp / "absent.env"

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._saved_env)
        config.DOTENV = self._saved_dotenv


class TestUserConfigRoundTrip(ConfigTestCase):
    def test_missing_config_is_empty(self) -> None:
        self.assertEqual(config.load_user_config(), {})

    def test_save_then_load(self) -> None:
        path = config.save_user_config({"store": "/tmp/models"})
        self.assertTrue(path.exists())
        self.assertEqual(config.load_user_config()["store"], "/tmp/models")

    def test_corrupt_config_does_not_raise(self) -> None:
        path = config.config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        self.assertEqual(config.load_user_config(), {})


class TestStoreResolution(ConfigTestCase):
    def test_env_wins_over_user_config(self) -> None:
        config.save_user_config({"store": str(self._tmp / "from-config")})
        os.environ["MODEL_STORE"] = str(self._tmp / "from-env")
        self.assertEqual(config.store_root(), (self._tmp / "from-env").resolve())

    def test_user_config_used_when_no_env(self) -> None:
        config.save_user_config({"store": str(self._tmp / "from-config")})
        self.assertEqual(config.store_root(), (self._tmp / "from-config").resolve())

    def test_falls_back_to_home(self) -> None:
        fake_home = self._tmp / "home"
        with mock.patch.object(config, "home", return_value=fake_home):
            self.assertEqual(config.store_root(), fake_home / "library")

    def test_store_is_configured(self) -> None:
        self.assertFalse(config.store_is_configured())
        config.save_user_config({"store": "/tmp/x"})
        self.assertTrue(config.store_is_configured())


class TestRegistryResolution(ConfigTestCase):
    def test_env_wins(self) -> None:
        target = self._tmp / "reg-env"
        target.mkdir()
        os.environ["VANITY_REGISTRY_DIR"] = str(target)
        self.assertEqual(config.registry_root(), target.resolve())

    def test_registry_follows_home(self) -> None:
        fake_home = self._tmp / "home"
        with mock.patch.object(config, "home", return_value=fake_home):
            self.assertEqual(config.registry_root(), fake_home / "registry")


class TestHomeResolution(ConfigTestCase):
    """One home directory backs registry, library, and state by default."""

    def test_env_wins(self) -> None:
        target = self._tmp / "from-env"
        os.environ["VANITY_HOME"] = str(target)
        self.assertEqual(config.home(), target.resolve())

    def test_user_config_next(self) -> None:
        target = self._tmp / "from-config"
        config.save_user_config({"home": str(target)})
        self.assertEqual(config.home(), target.resolve())

    def test_cwd_checkout_preferred(self) -> None:
        work = self._tmp / "clone"
        (work / "registry").mkdir(parents=True)
        with mock.patch.object(Path, "cwd", return_value=work):
            self.assertEqual(config.home(), work)

    def test_installed_falls_back_to_xdg_data(self) -> None:
        bare = self._tmp / "bare"
        bare.mkdir()
        data = self._tmp / "data"
        os.environ["XDG_DATA_HOME"] = str(data)
        with mock.patch.object(Path, "cwd", return_value=bare), \
             mock.patch.object(config, "ROOT", bare):
            self.assertEqual(config.home(), data / "vanity")

    def test_state_dir_follows_home(self) -> None:
        """An installed vanity must never write state into its own package dir."""
        fake_home = self._tmp / "home"
        with mock.patch.object(config, "home", return_value=fake_home):
            self.assertEqual(config.state_dir(), fake_home / ".state")


class TestFirstRunPrompt(ConfigTestCase):
    def test_no_prompt_without_a_tty(self) -> None:
        """The guard that keeps scripts and CI from hanging on input."""
        with mock.patch("sys.stdin.isatty", return_value=False), \
             mock.patch("builtins.input", side_effect=AssertionError("prompted!")):
            cli.ensure_store_configured("status")
        self.assertEqual(config.load_user_config(), {})

    def test_no_prompt_when_already_configured(self) -> None:
        config.save_user_config({"store": str(self._tmp / "already")})
        with mock.patch("sys.stdin.isatty", return_value=True), \
             mock.patch("sys.stdout.isatty", return_value=True), \
             mock.patch("builtins.input", side_effect=AssertionError("prompted!")):
            cli.ensure_store_configured("status")

    def test_no_prompt_for_commands_that_need_no_store(self) -> None:
        with mock.patch("sys.stdin.isatty", return_value=True), \
             mock.patch("sys.stdout.isatty", return_value=True), \
             mock.patch("builtins.input", side_effect=AssertionError("prompted!")):
            cli.ensure_store_configured("list")
            cli.ensure_store_configured("config")

    def test_prompt_saves_answer_and_creates_dir(self) -> None:
        chosen = self._tmp / "answered"
        with mock.patch("sys.stdin.isatty", return_value=True), \
             mock.patch("sys.stdout.isatty", return_value=True), \
             mock.patch("builtins.input", return_value=str(chosen)):
            cli.ensure_store_configured("fetch")

        self.assertTrue(chosen.is_dir(), "answered path should be created")
        self.assertEqual(config.load_user_config()["store"], str(chosen.resolve()))
        self.assertEqual(config.store_root(), chosen.resolve())

    def test_empty_answer_accepts_default(self) -> None:
        fake_root = self._tmp / "checkout"
        fake_root.mkdir()
        with mock.patch.object(config, "home", return_value=fake_root), \
             mock.patch("sys.stdin.isatty", return_value=True), \
             mock.patch("sys.stdout.isatty", return_value=True), \
             mock.patch("builtins.input", return_value=""):
            cli.ensure_store_configured("status")

            self.assertEqual(
                config.load_user_config()["store"], str(fake_root / "library")
            )
            self.assertTrue((fake_root / "library").is_dir())


if __name__ == "__main__":
    unittest.main()
