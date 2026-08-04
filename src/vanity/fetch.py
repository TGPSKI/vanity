"""Fetch dispatch: run_download and fetch_one entry points."""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING

from . import config, state

if TYPE_CHECKING:
    from .registry import Model

from .util import (
    FetchError,
    GitFailure,
    HttpFailure,
    bytes_on_disk,
    human_size,
    log,
    now,
    transient_error,
)


def _record_failure(model, message: str) -> None:
    state.update_state(model.key, {
        "repo": model.repo,
        "status": "failed",
        "updated_at": now(),
        "error": message,
    })


def fetch_one(model: Model, backend: str, retries: int, min_interval: float, quiet: bool) -> None:
    log(f"fetch {model.key} <- {model.repo} ({model.size_hint})")
    config.store_root().mkdir(parents=True, exist_ok=True)
    state.acquire_lock(model)
    try:
        previous_attempt = 0.0
        for attempt in range(1, retries + 2):
            elapsed = time.monotonic() - previous_attempt if previous_attempt else min_interval
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            previous_attempt = time.monotonic()

            try:
                run_download(model, backend=backend, quiet=quiet, min_interval=min_interval)
            except HttpFailure as error:
                message = str(error)
                should_retry = error.status in {429, 500, 502, 503, 504} or transient_error(message)
                if not should_retry or attempt > retries:
                    _record_failure(model, message)
                    raise
                sleep_for = error.retry_after or min(900, (2 ** attempt) + random.uniform(0, 3))
                log(f"retry {model.key}: attempt {attempt}/{retries} failed; sleeping {sleep_for:.1f}s")
                time.sleep(sleep_for)
            except GitFailure as error:
                should_retry = transient_error(error.stderr or "")
                if not should_retry or attempt > retries:
                    _record_failure(model, str(error))
                    raise
                sleep_for = min(900, (2 ** attempt) + random.uniform(0, 3))
                log(f"retry {model.key}: attempt {attempt}/{retries} failed; sleeping {sleep_for:.1f}s")
                time.sleep(sleep_for)
            except FetchError:
                raise
            else:
                fetched_bytes = bytes_on_disk(model.path)
                state.update_state(model.key, {
                    "repo": model.repo,
                    "revision": model.revision,
                    "backend": backend,
                    "status": "fetched",
                    "updated_at": now(),
                    "path": str(model.path),
                    "bytes": str(fetched_bytes),
                })
                state.write_metadata(model)
                log(f"ok {model.key}: {human_size(fetched_bytes)}")
                return
    finally:
        state.release_lock(model)


def run_download(model: Model, backend: str, quiet: bool, min_interval: float) -> None:
    from .gitfetch import run_git_download
    from .httpfetch import run_http_download
    if backend == "http":
        run_http_download(model, quiet=quiet, min_interval=min_interval)
        return
    if backend in {"git-xet", "git-lfs"}:
        run_git_download(model, backend=backend, quiet=quiet)
        return
    raise FetchError(f"unknown backend: {backend}")
