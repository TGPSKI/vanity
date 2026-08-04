"""HTTP-based downloading from Hugging Face."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .config import heartbeat_seconds
from .util import (
    FetchError,
    HttpFailure,
    display_path,
    human_duration,
    human_size,
    log,
)

if TYPE_CHECKING:
    from .registry import Model


def hf_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    from . import __version__
    from .config import token

    headers = {"User-Agent": f"vanity/{__version__}"}
    auth = token()
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    if extra:
        headers.update(extra)
    return headers


def _as_http_failure(url: str, error: Exception) -> HttpFailure:
    """Normalise urllib's three failure shapes into one HttpFailure."""
    if isinstance(error, HTTPError):
        detail = error.read().decode("utf-8", errors="replace")[:500]
        return HttpFailure(url, error.code, detail or error.reason, read_retry_after(error))
    if isinstance(error, URLError):
        return HttpFailure(url, None, str(error.reason))
    return HttpFailure(url, None, "timeout")


def read_retry_after(error: HTTPError) -> float | None:
    value = error.headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def request_bytes(url: str, headers: dict[str, str] | None = None, timeout: float = 45.0) -> bytes:
    request = Request(url, headers=hf_headers(headers))
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except (HTTPError, URLError, TimeoutError) as error:
        raise _as_http_failure(url, error) from error


def repo_tree(model: Model) -> list[dict[str, object]]:
    # safe="/" is required: the API rejects an encoded slash in org/repo with
    # "Invalid repo name: ... repo name includes an url-encoded slash".
    repo = quote(model.repo, safe="/")
    revision = quote(model.revision, safe="")
    url = f"https://huggingface.co/api/models/{repo}/tree/{revision}?recursive=1&expand=1"
    payload = json.loads(request_bytes(url).decode("utf-8"))
    if isinstance(payload, dict) and "siblings" in payload:
        payload = payload["siblings"]
    if not isinstance(payload, list):
        raise FetchError(f"unexpected tree response for {model.repo}")
    return [item for item in payload if item.get("type") == "file" or "rfilename" in item or "path" in item]


def tree_path(item: dict[str, object]) -> str:
    path = item.get("path") or item.get("rfilename")
    if not isinstance(path, str) or not path:
        raise FetchError(f"tree entry has no file path: {item}")
    return path


def tree_size(item: dict[str, object]) -> int | None:
    size = item.get("size")
    if isinstance(size, int):
        return size
    lfs = item.get("lfs")
    if isinstance(lfs, dict) and isinstance(lfs.get("size"), int):
        return lfs["size"]
    return None


def resolve_url(model: Model, file_path: str) -> str:
    repo = quote(model.repo, safe="/")
    revision = quote(model.revision, safe="")
    quoted_path = quote(file_path)
    return f"https://huggingface.co/{repo}/resolve/{revision}/{quoted_path}"


def should_skip_file(path: str) -> bool:
    return Path(path).name in {".gitattributes"}


def download_file(url: str, destination: Path, expected_size: int | None, quiet: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")

    if expected_size is not None and destination.exists() and destination.stat().st_size == expected_size:
        return

    offset = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    request = Request(url, headers=hf_headers(headers))

    try:
        with urlopen(request, timeout=90.0) as response:
            status = getattr(response, "status", 200)
            mode = "ab" if offset and status == 206 else "wb"
            if offset and status != 206:
                offset = 0
            if not quiet:
                action = "resume" if offset else "download"
                log(f"{action} {display_path(destination)}")
            start = time.monotonic()
            next_heartbeat = start + heartbeat_seconds()
            written = 0
            with part.open(mode) as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)
                    current = time.monotonic()
                    if not quiet and current >= next_heartbeat:
                        log(
                            f"alive http {display_path(destination)}: "
                            f"elapsed={human_duration(current - start)} "
                            f"downloaded={human_size(offset + written)}"
                        )
                        next_heartbeat = current + heartbeat_seconds()
    except (HTTPError, URLError, TimeoutError) as error:
        raise _as_http_failure(url, error) from error

    if expected_size is not None and part.stat().st_size != expected_size:
        raise HttpFailure(url, None, f"incomplete download: got {part.stat().st_size}, expected {expected_size}")
    part.replace(destination)


def run_http_download(model: Model, quiet: bool, min_interval: float) -> None:
    files = repo_tree(model)
    if not files:
        raise FetchError(f"no downloadable files found for {model.repo}")
    if not quiet:
        log(f"{model.key}: {len(files)} files")
    previous_request = 0.0
    for item in files:
        file_path = tree_path(item)
        if should_skip_file(file_path):
            continue
        elapsed = time.monotonic() - previous_request if previous_request else min_interval
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        previous_request = time.monotonic()
        download_file(resolve_url(model, file_path), model.path / file_path, tree_size(item), quiet)