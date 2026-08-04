"""Configuration constants and environment helpers.

Resolution order for the model store and the registry directory:

    1. an explicit CLI flag (registry only)
    2. environment / .env
    3. the user config file (~/.config/vanity/config.json)
    4. a checkout-local default, when running from a source tree
    5. a first-run prompt, when attached to a terminal

ROOT is the source checkout the package was imported from. That is the right
default when running from a clone, but an installed `vanity` can be invoked
from anywhere -- which is what the user config file is for.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DOTENV = ROOT / ".env"


def _looks_like_checkout(path: Path) -> bool:
    """A directory laid out like a vanity clone (its registry lives inside)."""
    return (path / "registry").is_dir()


def home() -> Path:
    """The one directory vanity keeps things in.

    Everything else -- registry, library, state -- defaults to a subdirectory of
    this, so a normal setup configures exactly one path. Resolution:
    $VANITY_HOME, then the user config, then the checkout when running from a
    clone, then the XDG data dir for an installed vanity.
    """
    configured = env_value("VANITY_HOME")
    if configured:
        return expand_path(configured)
    saved = load_user_config().get("home")
    if saved:
        return expand_path(str(saved))
    if _looks_like_checkout(Path.cwd()):
        return Path.cwd()
    if _looks_like_checkout(ROOT):
        return ROOT
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base).expanduser() if base else Path.home() / ".local" / "share") / "vanity"


def state_dir() -> Path:
    """Where locks and fetch state live. Derived from home() so an installed
    vanity never writes into its own site-packages directory."""
    return home() / ".state"


def config_home() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base).expanduser() if base else Path.home() / ".config") / "vanity"


def config_file() -> Path:
    return config_home() / "config.json"


def load_user_config() -> dict:
    path = config_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_user_config(values: dict) -> Path:
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(values, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)
    return path


def env_value(name: str) -> str | None:
    return os.environ.get(name) or dotenv_values().get(name)


def expand_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def store_root() -> Path:
    configured = env_value("MODEL_STORE") or env_value("MODELS_DIR")
    if configured:
        return expand_path(configured)
    saved = load_user_config().get("store")
    if saved:
        return expand_path(str(saved))
    return home() / "library"


def registry_root() -> Path:
    configured = env_value("VANITY_REGISTRY_DIR")
    if configured:
        return expand_path(configured)
    saved = load_user_config().get("registry_dir")
    if saved:
        return expand_path(str(saved))
    return home() / "registry"


def store_is_configured() -> bool:
    """True when the store comes from an explicit source, not just the default."""
    return bool(
        env_value("MODEL_STORE")
        or env_value("MODELS_DIR")
        or load_user_config().get("store")
    )


def dotenv_values() -> dict[str, str]:
    if not DOTENV.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in DOTENV.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            values[key] = value
    return values


def token() -> str | None:
    return (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or env_value("HF_TOKEN")
        or env_value("HUGGING_FACE_HUB_TOKEN")
    )


def heartbeat_seconds() -> float:
    raw = env_value("VANITY_HEARTBEAT_SECONDS")
    if not raw:
        return 60.0
    try:
        return max(5.0, float(raw))
    except ValueError:
        return 60.0