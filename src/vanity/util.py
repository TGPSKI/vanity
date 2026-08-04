"""General utilities: timestamps, logging, size/duration helpers, transient error detection."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


class FetchError(RuntimeError):
    pass


class HttpFailure(FetchError):
    def __init__(self, url: str, status: int | None, message: str, retry_after: float | None = None):
        self.url = url
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"{status or 'network'} {message}: {url}")


class GitFailure(FetchError):
    def __init__(self, returncode: int, cmd: list[str], stderr: str):
        self.returncode = returncode
        self.cmd = cmd
        self.stderr = stderr
        last_non_empty = ""
        for line in reversed(stderr.splitlines()):
            stripped = line.strip()
            if stripped:
                last_non_empty = stripped
                break
        super().__init__(
            f"git exit {returncode}: {last_non_empty or '(no stderr)'}"
        )


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(message: str) -> None:
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"[{stamp}] {message}", flush=True)


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{value:.1f}TB"


def human_duration(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def transient_error(text: str) -> bool:
    lowered = text.lower()
    needles = (
        "429",
        "rate limit",
        "too many requests",
        "timeout",
        "timed out",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "502",
        "503",
        "504",
        "5xx",
    )
    return any(needle in lowered for needle in needles)


def bytes_on_disk(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def pid_alive(pid: int) -> bool:
    """Whether a process is running, without signalling it.

    POSIX uses the signal-0 probe. Windows has no such thing -- os.kill there
    terminates rather than probes -- so ask the OS for a handle instead.
    """
    if pid <= 0:
        return False

    if os.name == "nt":  # pragma: no cover - exercised only on Windows
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # running, just owned by someone else
    return True


def command_ok(command: list[str]) -> bool:
    """Whether a command runs and exits zero. Used for tool availability probes."""
    try:
        return subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode == 0
    except (FileNotFoundError, OSError):
        return False


def any_command_ok(commands) -> bool:
    return any(command_ok(command) for command in commands)


def display_path(path: Path) -> str:
    """Path for humans: relative to the store when it is under it, else absolute.

    Never use Path.relative_to for display -- it raises when the store lives
    outside the checkout, which is the normal case once a library is configured.
    """
    from .config import store_root

    try:
        return str(path.relative_to(store_root()))
    except ValueError:
        return str(path)