"""Registry loader: parse JSON files, validate model entries, resolve targets."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Model:
    key: str
    repo: str
    size_hint: str
    runtime: str
    role: str
    revision: str = "main"

    @property
    def path(self) -> Path:
        from .config import store_root
        return store_root() / self.key


MODEL_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
ALLOWED_MODEL_FIELDS = {"repo", "runtime", "role", "size_hint", "revision"}
ALLOWED_TOP_FIELDS = {"$schema", "description", "models", "sets", "aliases"}


@dataclass(frozen=True)
class Registry:
    models: dict[str, Model]
    sets: dict[str, list[str]]
    aliases: dict[str, str]
    files: dict[str, list[str]]
    # per-file "description", so `list` can say what each file is for
    descriptions: dict[str, str] = field(default_factory=dict)


def _model_from_raw(key, raw, file_name):
    """Parse and validate a single model entry."""
    unk = set(raw.keys()) - ALLOWED_MODEL_FIELDS
    if unk:
        msg = (
            f"{file_name}: unexpected fields for "
            + repr(key) + ": " + str(sorted(unk))
        )
        raise SystemExit(msg)
    req = ("repo", "runtime", "role")
    miss = [x for x in req if x not in raw]
    if miss:
        msg = (
            f"{file_name}: model " + repr(key)
            + " missing " + str(miss)
        )
        raise SystemExit(msg)
    repo = raw["repo"]
    if not isinstance(repo, str) or "/" not in repo:
        msg = (
            f"{file_name}: model " + repr(key)
            + ": bad repo " + repr(repo)
        )
        raise SystemExit(msg)
    return Model(
        key=key,
        repo=repo,
        size_hint=raw.get("size_hint", ""),
        runtime=raw["runtime"],
        role=raw["role"],
        revision=raw.get("revision", "main"),
    )


def load_registry(registry_dir):
    """Load registry directory. Raises SystemExit on validation errors."""
    if not registry_dir.is_dir():
        raise SystemExit(
            f"no registry yet at {registry_dir}\n"
            "  add your first model:  vanity add <org>/<repo>\n"
            "  or point vanity at an existing one:  vanity config --home PATH"
        )

    flist = sorted(registry_dir.glob("*.json"), key=lambda p: p.name)
    if not flist:
        raise SystemExit(
            f"registry is empty: {registry_dir}\n"
            "  add your first model:  vanity add <org>/<repo>"
        )

    models = {}
    files_map = {}
    key_to_stem = {}
    raw_sets = {}
    raw_aliases = {}
    descriptions = {}
    # file each set/alias came from, for error messages in the validation pass
    set_origin = {}
    alias_origin = {}

    for fp in flist:
        stem = fp.stem
        data = json.loads(fp.read_text())

        extra = set(data.keys()) - ALLOWED_TOP_FIELDS
        if extra:
            raise SystemExit(
                f"{fp.name}: unexpected top-level: {sorted(extra)}"
            )

        if data.get("description"):
            descriptions[stem] = str(data["description"])

        fk = []
        for key, raw in (data.get("models") or {}).items():
            if key in models:
                old = key_to_stem.get(key, "?")
                raise SystemExit(
                    "duplicate model key "
                    + repr(key)
                    + " in " + fp.name + " and " + old
                )
            if not MODEL_KEY_RE.match(key):
                raise SystemExit(
                    f"{fp.name}: bad key pattern: " + repr(key)
                )
            mdl = _model_from_raw(key, raw, fp.name)
            models[key] = mdl
            key_to_stem[key] = fp.name
            fk.append(key)
        files_map[stem] = fk

        for sn, mb in (data.get("sets") or {}).items():
            if not isinstance(mb, list):
                raise SystemExit(
                    f"{fp.name}: set " + repr(sn) + " must be array"
                )
            if sn in raw_sets:
                raise SystemExit("duplicate set name: " + sn)
            # Members are checked once every file has been read, so a set may
            # reference models declared in any file -- not only earlier ones.
            raw_sets[sn] = mb
            set_origin[sn] = fp.name

        for an, tg in (data.get("aliases") or {}).items():
            # Note: alias keys can have uppercase (legacy support)
            if an in raw_aliases:
                raise SystemExit(
                    f"{fp.name}: duplicate alias " + repr(an)
                )
            alias_origin[an] = fp.name
            if not MODEL_KEY_RE.match(tg):
                msg = (
                    f"{fp.name}: bad alias target for "
                    + repr(an) + ": " + repr(tg)
                )
                raise SystemExit(msg)
            raw_aliases[an] = tg

    # Second pass: every name is known now, so cross-file references resolve.
    for sn, members in raw_sets.items():
        if sn in files_map:
            raise SystemExit(
                f"{set_origin[sn]}: set {sn!r} collides with a registry file name"
            )
        for member in members:
            if member not in models:
                raise SystemExit(
                    f"{set_origin[sn]}: set {sn!r} references unknown {member!r}"
                )

    for an in raw_aliases:
        if an in models or an in raw_sets or an in files_map:
            raise SystemExit(
                f"{alias_origin[an]}: alias {an!r} shadows an existing name"
            )

    # Validate alias chains resolve to known models
    resolved = {}
    for an, target in raw_aliases.items():
        cur = target
        seen_chain = set()
        while cur in raw_aliases:
            if cur in seen_chain:
                raise SystemExit(
                    "alias chain cycle involving " + repr(an)
                )
            seen_chain.add(cur)
            cur = raw_aliases[cur]
        if cur not in models:
            raise SystemExit(
                "alias " + repr(an)
                + " -> unknown " + repr(cur)
            )
        resolved[an] = cur

    return Registry(
        models=models,
        sets=raw_sets,
        aliases=resolved,
        files=files_map,
        descriptions=descriptions,
    )


def require_model(key, registry):
    """Resolve model key -> alias."""
    k = key.strip()
    if k in registry.models:
        return registry.models[k]
    if k in registry.aliases:
        return require_model(registry.aliases[k], registry)
    raise SystemExit(
        f"unknown target {k!r}. Known: {len(registry.models)} models, "
        f"{len(registry.aliases)} aliases, "
        f"sets {sorted(registry.sets)}, files {sorted(registry.files)}"
    )


def expand_targets(names, registry):
    """Expand CLI targets: model -> alias -> set -> file -> all."""
    keys = []

    def _one(name):
        if name in registry.models:
            return [name]
        if name in registry.aliases:
            return _one(registry.aliases[name])
        if name in registry.sets:
            return registry.sets[name]
        if name in registry.files:
            return registry.files[name]
        if name == "all":
            ordered = []
            for s in registry.files:
                ordered.extend(registry.files[s])
            return ordered
        ss = ", ".join(sorted(registry.sets.keys()))
        sf = ", ".join(sorted(registry.files.keys()))
        sa = ", ".join(sorted(registry.aliases.keys()))
        sm = ", ".join(sorted(registry.models.keys()))
        raise SystemExit(
            "unknown target " + repr(name)
            + ".\n  known sets: " + ss
            + "\n  known files: " + sf
            + "\n  known aliases: " + sa
            + "\n  known models: " + sm
        )

    for name in names:
        keys.extend(_one(name))

    seen = set()
    out = []
    for k in keys:
        if k not in seen:
            out.append(registry.models[k])
            seen.add(k)
    return out