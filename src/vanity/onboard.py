"""Onboarding a new model: resolve a Hugging Face repo and derive registry fields.

`vanity add` leans on this so a new entry is filled in from what the repo
actually reports -- size, current commit, quantization, pipeline -- rather than
from someone guessing and typing it in. Everything here is stdlib only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote

from .util import FetchError, human_size

HF_HOSTS = ("huggingface.co", "hf.co", "www.huggingface.co")

# Quantization labels worth surfacing in `runtime`, most specific first so
# "nvfp4" wins over a bare "4-bit" on the same repo.
QUANT_TAGS = (
    ("nvfp4", "NVFP4"),
    ("mxfp4", "MXFP4"),
    ("fp8", "FP8"),
    ("awq", "AWQ"),
    ("gptq", "GPTQ"),
    ("int8", "INT8"),
    ("8-bit", "8-bit"),
    ("4-bit", "4-bit"),
)

ROLE_BY_PIPELINE = {
    "text-generation": "text generation",
    "text2text-generation": "text generation",
    "conversational": "chat",
    "sentence-similarity": "embedding",
    "feature-extraction": "embedding",
    "text-classification": "classifier",
    "token-classification": "classifier",
    "fill-mask": "encoder classifier",
    "automatic-speech-recognition": "speech-to-text",
    "text-to-speech": "text-to-speech",
    "text-to-audio": "text-to-speech",
    "image-text-to-text": "vision-language",
    "visual-question-answering": "vision-language",
    "image-to-text": "vision-language",
}


def parse_repo_ref(text: str) -> str:
    """Accept org/name, a full URL, or hf.co/org/name -> 'org/name'."""
    ref = (text or "").strip()
    if not ref:
        raise FetchError("no repository given")

    ref = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", ref)
    for host in HF_HOSTS:
        if ref.startswith(host + "/"):
            ref = ref[len(host) + 1:]
            break

    # drop /tree/<rev>, /blob/..., query strings and trailing slashes
    ref = ref.split("?")[0].split("#")[0].strip("/")
    for marker in ("/tree/", "/blob/", "/resolve/"):
        if marker in ref:
            ref = ref.split(marker)[0]

    parts = [p for p in ref.split("/") if p]
    if len(parts) != 2:
        raise FetchError(
            f"expected a Hugging Face repo as 'org/name' (or its URL), got {text!r}"
        )
    return "/".join(parts)


def suggest_key(repo: str) -> str:
    """Registry key from the repo name, conforming to MODEL_KEY_RE."""
    name = repo.split("/")[-1].lower()
    name = name.replace(".", "")
    name = re.sub(r"[^a-z0-9._-]+", "-", name).strip("-")
    if not name or not name[0].isalnum():
        name = f"m-{name}".strip("-")
    return name


def detect_quant(repo: str, tags: list[str]) -> str | None:
    haystack = f"{repo.lower()} {' '.join(t.lower() for t in tags)}"
    for needle, label in QUANT_TAGS:
        if needle in haystack:
            return label
    return None


def suggest_runtime(repo: str, info: dict, filenames: list[str]) -> str:
    tags = [str(t).lower() for t in info.get("tags") or []]
    pipeline = (info.get("pipeline_tag") or "").lower()
    library = (info.get("library_name") or "").lower()
    quant = detect_quant(repo, tags)

    if any(f.lower().endswith(".gguf") for f in filenames) or "gguf" in tags:
        return "llama.cpp / GGUF"
    # Real ct2 repos report library_name/tag "ctranslate2"; the name patterns
    # are a fallback for conversions that set neither.
    lowered_repo = repo.lower()
    if (
        "ctranslate2" in tags
        or library == "ctranslate2"
        or "ctranslate2" in lowered_repo
        or lowered_repo.endswith("-ct2")
        or "faster-whisper" in lowered_repo
    ):
        return "CTranslate2 / faster-whisper"
    if library == "sentence-transformers" or pipeline in {"sentence-similarity", "feature-extraction"}:
        return "sentence-transformers / TEI"
    if pipeline == "automatic-speech-recognition":
        return "transformers / ASR"
    if pipeline in {"text-to-speech", "text-to-audio"}:
        return "TTS runtime"
    if pipeline in {"text-classification", "token-classification", "fill-mask"}:
        return "transformers / encoder"

    has_safetensors = any(f.lower().endswith(".safetensors") for f in filenames)
    if has_safetensors or "safetensors" in tags:
        return f"vLLM / {quant}" if quant else "vLLM / BF16"
    return f"transformers / {quant}" if quant else "transformers"


def suggest_role(info: dict) -> str:
    pipeline = (info.get("pipeline_tag") or "").lower()
    return ROLE_BY_PIPELINE.get(pipeline, pipeline.replace("-", " ") or "unspecified")


def suggest_size_hint(total_bytes: int | None) -> str:
    if not total_bytes:
        return ""
    return f"~{human_size(total_bytes)}"


def model_info(repo: str) -> dict:
    """Repo metadata straight from the HF API. Raises HttpFailure on 404/403."""
    from .httpfetch import request_bytes

    url = f"https://huggingface.co/api/models/{quote(repo, safe='/')}"
    payload = json.loads(request_bytes(url).decode("utf-8"))
    if not isinstance(payload, dict):
        raise FetchError(f"unexpected metadata response for {repo}")
    return payload


def repo_files_and_size(repo: str, revision: str) -> tuple[list[str], int | None]:
    """File names and total size from the tree API, for the size hint."""
    from .httpfetch import request_bytes, tree_path, tree_size

    url = (
        f"https://huggingface.co/api/models/{quote(repo, safe='/')}"
        f"/tree/{quote(revision, safe='')}?recursive=1&expand=1"
    )
    payload = json.loads(request_bytes(url).decode("utf-8"))
    if not isinstance(payload, list):
        return [], None

    names: list[str] = []
    total = 0
    measured = False
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            names.append(tree_path(item))
        except FetchError:
            continue
        size = tree_size(item)
        if size:
            total += size
            measured = True
    return names, (total if measured else None)


def describe(repo: str) -> dict:
    """Everything `vanity add` needs about a repo, in one call site."""
    info = model_info(repo)
    revision = info.get("sha") or "main"
    filenames, total = repo_files_and_size(repo, revision)
    if not filenames:
        filenames = [
            s.get("rfilename", "")
            for s in info.get("siblings") or []
            if isinstance(s, dict)
        ]

    return {
        "repo": repo,
        "key": suggest_key(repo),
        "revision": revision,
        "runtime": suggest_runtime(repo, info, filenames),
        "role": suggest_role(info),
        "size_hint": suggest_size_hint(total),
        "gated": bool(info.get("gated")),
        "file_count": len(filenames),
        "total_bytes": total,
    }


def add_to_registry_file(path: Path, key: str, entry: dict) -> None:
    """Insert a model into a registry file, atomically, keeping its layout."""
    document = json.loads(path.read_text()) if path.exists() else {"models": {}}

    models = document.setdefault("models", {})
    if key in models:
        raise FetchError(f"{path.name} already defines {key!r}")
    models[key] = entry

    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(document, indent=2) + "\n")
    tmp.replace(path)
