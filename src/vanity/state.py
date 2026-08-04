"""Fetch state management: lock-file–based locking and state persistence."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from . import config
from .util import FetchError, log, now, pid_alive

if TYPE_CHECKING:
    from .registry import Model


def _state_dir() -> Path:
    """Resolved at call time so tests and config changes both take effect."""
    return config.state_dir()


def _fetch_state() -> Path:
    return _state_dir() / "fetches.json"


def load_state() -> dict[str, dict[str, str]]:
    fetch_state = _fetch_state()
    if not fetch_state.exists():
        return {}
    return json.loads(fetch_state.read_text())


def save_state(state: dict[str, dict[str, str]]) -> None:
    _state_dir().mkdir(parents=True, exist_ok=True)
    fetch_state = _fetch_state()
    tmp = fetch_state.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    tmp.replace(fetch_state)


_STATE_LOCK = threading.Lock()


def update_state(key: str, entry: dict[str, str]) -> None:
    """Atomically update a single key in the state file."""
    with _STATE_LOCK:
        state = load_state()
        state[key] = entry
        save_state(state)


def lock_path(model: Model) -> Path:
    return _state_dir() / f"{model.key}.lock"


def _claim(lockfile: Path) -> bool:
    """Create the lock exclusively and record our PID. False if it already exists."""
    try:
        fd = os.open(lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as handle:
        handle.write(f"{os.getpid()} {now()}\n")
    return True


def _lock_holder(lockfile: Path) -> int | None:
    """PID recorded in the lock file, or None if unreadable/garbage."""
    try:
        parts = lockfile.read_text().strip().split()
        return int(parts[0]) if parts else None
    except (OSError, ValueError):
        return None


def acquire_lock(model: Model) -> None:
    _state_dir().mkdir(parents=True, exist_ok=True)
    lockfile = lock_path(model)

    if _claim(lockfile):
        return

    held_by = _lock_holder(lockfile)
    if held_by is not None and not pid_alive(held_by):
        log(f"breaking stale lock for {model.key} (pid {held_by} dead)")
        lockfile.unlink(missing_ok=True)
        if _claim(lockfile):
            return

    # Alive, or a lock we cannot interpret -- either way, do not touch it.
    raise FetchError(
        f"{model.key} already has an active fetch lock ({lockfile}) -- "
        "remove manually to override"
    ) from None


def release_lock(model: Model) -> None:
    lock_path(model).unlink(missing_ok=True)


def write_metadata(model: Model) -> None:
    metadata = {
        "key": model.key,
        "repo": model.repo,
        "revision": model.revision,
        "size_hint": model.size_hint,
        "runtime": model.runtime,
        "role": model.role,
        "fetched_at": now(),
    }
    model.path.mkdir(parents=True, exist_ok=True)
    (model.path / ".vanity.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")