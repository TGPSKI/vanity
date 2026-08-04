"""Git-based download backends (git-lfs, git-xet)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .util import (
    FetchError,
    GitFailure,
    any_command_ok,
    bytes_on_disk,
    human_duration,
    human_size,
    log,
)

if TYPE_CHECKING:
    from .registry import Model


# A revision that is a raw commit SHA needs different handling: `git clone
# --branch` accepts branch and tag names only and fails with "Remote branch
# <sha> not found in upstream origin". Hex-only names of 7+ chars are treated
# as commits -- git itself carries the same branch-vs-abbrev-SHA ambiguity.
SHA_RE = re.compile(r"[0-9a-f]{7,40}")


def is_commit_sha(revision: str) -> bool:
    return bool(SHA_RE.fullmatch(revision or ""))


def askpass_path() -> Path:
    from .config import state_dir
    state = state_dir()
    state.mkdir(parents=True, exist_ok=True)
    path = state / "hf-askpass.sh"
    if not path.exists():
        path.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  *Username*) printf '%s\\n' \"${HF_GIT_USERNAME:-hf_user}\" ;;\n"
            "  *) printf '%s\\n' \"$HF_TOKEN\" ;;\n"
            "esac\n"
        )
        path.chmod(0o700)
    return path


def git_env(use_token: bool) -> dict[str, str]:
    from .config import token
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if use_token and token():
        env["HF_TOKEN"] = token() or ""
        env["GIT_ASKPASS"] = str(askpass_path())
    return env


class SizeSampler:
    """Caches a size-walk result and recomputes every N invocations."""

    def __init__(self, path: Path, walker=bytes_on_disk, every: int = 5):
        self._path = path
        self._walker = walker
        self._every = every
        self._size: int | None = None
        self._count: int = 0

    def sample(self) -> tuple[int, bool]:
        self._count += 1
        if self._size is None or (self._count - 1) % self._every == 0:
            self._size = self._walker(self._path)
            return self._size, True
        return self._size, False


def run_git_command(cmd: list[str], use_token: bool, model: Model, quiet: bool) -> None:
    from .config import heartbeat_seconds
    sampler = SizeSampler(model.path)
    with tempfile.TemporaryFile() as stderr_buf:
        process = subprocess.Popen(
            cmd, env=git_env(use_token), stderr=stderr_buf
        )
        start = time.monotonic()
        next_heartbeat = start + heartbeat_seconds()
        while True:
            return_code = process.poll()
            if return_code is not None:
                if return_code:
                    stderr_buf.seek(0)
                    stderr_raw = stderr_buf.read()
                    stderr_str = stderr_raw.decode("utf-8", errors="replace")[-4000:]
                    raise GitFailure(returncode=return_code, cmd=cmd, stderr=stderr_str)
                return

            current = time.monotonic()
            if not quiet and current >= next_heartbeat:
                size, fresh = sampler.sample()
                label = f"size={human_size(size)}" if fresh else f"size~={human_size(size)}"
                log(
                    f"alive {model.key}: pid={process.pid} "
                    f"elapsed={human_duration(current - start)} "
                    f"{label}"
                )
                next_heartbeat = current + heartbeat_seconds()
            time.sleep(1)


def remove_empty_failed_clone(path: Path) -> None:
    if path.exists() and path.is_dir() and not any(path.iterdir()):
        path.rmdir()


def run_git_download(model: Model, backend: str, quiet: bool) -> None:
    from .config import token as get_token

    if not shutil.which("git"):
        raise FetchError(f"{backend} backend requested but git is not installed")

    if backend == "git-xet":
        probes = (["git", "xet", "--version"], ["git", "xet", "version"], ["git-xet", "--version"])
        missing = "git-xet backend requested but git xet is not installed"
    else:
        probes = (["git", "lfs", "version"],)
        missing = "git-lfs backend requested but git lfs is not installed"

    if not any_command_ok(probes):
        raise FetchError(missing)

    url = f"https://huggingface.co/{model.repo}"

    pinned = is_commit_sha(model.revision)
    checkout_cmd = None

    if (model.path / ".git").exists():
        if pinned:
            # `pull --ff-only` fails on a detached HEAD, so fetch then re-detach.
            cmd = ["git", "-C", str(model.path), "fetch", "origin"]
            checkout_cmd = ["git", "-C", str(model.path), "checkout", "--detach", model.revision]
        else:
            cmd = ["git", "-C", str(model.path), "pull", "--ff-only"]
        restore_cmd = ["git", "-C", str(model.path), "restore", "--source=HEAD", ":/"]
    elif model.path.exists() and any(model.path.iterdir()):
        raise FetchError(f"{model.path} exists and is not an empty git checkout")
    else:
        model.path.parent.mkdir(parents=True, exist_ok=True)
        if pinned:
            cmd = ["git", "clone", url, str(model.path)]
            checkout_cmd = ["git", "-C", str(model.path), "checkout", "--detach", model.revision]
        else:
            cmd = ["git", "clone", "--branch", model.revision, url, str(model.path)]
        restore_cmd = None

    steps: list[tuple[list[str], str | None]] = [(cmd, None)]
    if checkout_cmd:
        steps.append((checkout_cmd, f"{model.key}: checkout pinned revision {model.revision}"))
    if restore_cmd:
        steps.append((restore_cmd, f"{model.key}: hydrate working tree from HEAD"))

    def run_steps(use_token: bool) -> None:
        for step_cmd, message in steps:
            if message and not quiet:
                log(message)
            run_git_command(step_cmd, use_token=use_token, model=model, quiet=quiet)

    if not quiet:
        log(f"{model.key}: {backend} {'update' if (model.path / '.git').exists() else 'clone'} {url}")
    try:
        run_steps(use_token=bool(get_token()))
    except GitFailure:
        if not get_token():
            raise
        if not quiet:
            log(f"{model.key}: token-auth failed; retrying without token for public repo access")
        if cmd[:2] == ["git", "clone"]:
            remove_empty_failed_clone(model.path)
        run_steps(use_token=False)


def git_checkout_complete(path: Path) -> bool:
    if not (path / ".git").exists():
        return True
    try:
        output = subprocess.check_output(
            ["git", "-C", str(path), "status", "--porcelain=v1"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    dirty = [line for line in output.splitlines() if line != "?? .vanity.json"]
    return not dirty