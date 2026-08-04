"""CLI: argument parsing and command implementations."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import shutil
import sys
from pathlib import Path

from . import config, gitfetch, onboard
from . import fetch as fetch_mod
from . import registry as registry_mod
from . import state as state_mod
from .util import (
    FetchError,
    any_command_ok,
    bytes_on_disk,
    command_ok,
    human_size,
    log,
    now,
)


def _reg_dir(args) -> Path:
    """Return registry dir: flag -> env -> user config -> cwd -> checkout."""
    val = getattr(args, "registry_dir", None)
    if val is not None:
        return Path(val)
    return config.registry_root()


# Commands that read or write the model library, and so need a store to exist.
STORE_COMMANDS = {"status", "doctor", "fetch", "verify", "remove"}


def ensure_store_configured(command: str) -> None:
    """On first run, ask where models should live and remember the answer.

    Only prompts when a store has not been configured by any other means and
    the process is attached to a terminal -- scripts, pipes, and CI silently
    fall back to the checkout-local default instead of hanging on input.
    """
    if command not in STORE_COMMANDS or config.store_is_configured():
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return

    default = config.store_root()
    print("vanity: first run -- where should the model library live?")
    print(f"  Enter to accept the default: {default}")
    try:
        raw = input("  model library path: ").strip()
    except EOFError:
        print()
        return

    chosen = config.expand_path(raw) if raw else default
    try:
        chosen.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SystemExit(f"cannot create {chosen}: {error}") from error

    values = config.load_user_config()
    values["store"] = str(chosen)
    saved_to = config.save_user_config(values)
    print(f"  store set to {chosen}")
    print(f"  saved to {saved_to} (change it any time with `vanity config --store PATH`)")
    print()


def cmd_config(args: argparse.Namespace) -> int:
    values = config.load_user_config()
    changed = False

    if args.home is not None:
        path = config.expand_path(args.home)
        path.mkdir(parents=True, exist_ok=True)
        values["home"] = str(path)
        changed = True
    if args.store is not None:
        path = config.expand_path(args.store)
        path.mkdir(parents=True, exist_ok=True)
        values["store"] = str(path)
        changed = True
    if args.registry_dir_set is not None:
        values["registry_dir"] = str(config.expand_path(args.registry_dir_set))
        changed = True

    if changed:
        print(f"saved {config.save_user_config(values)}")

    def source(env_names: tuple[str, ...], key: str) -> str:
        for name in env_names:
            if config.env_value(name):
                return f"env {name}"
        if config.load_user_config().get(key):
            return "user config"
        return "default"

    print(f"config file  {config.config_file()}")
    print(f"home         {config.home()}  ({source(('VANITY_HOME',), 'home')})")
    print(f"store        {config.store_root()}  ({source(('MODEL_STORE', 'MODELS_DIR'), 'store')})")
    print(f"registry     {config.registry_root()}  ({source(('VANITY_REGISTRY_DIR',), 'registry_dir')})")
    print(f"state        {config.state_dir()}")
    return 0


def _ask(prompt: str, default: str = "") -> str:
    """Prompt with a default shown; Enter accepts it."""
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"  {prompt}{suffix}: ").strip()
    except EOFError:
        print()
        return default
    return answer or default


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _load_or_bootstrap(registry_dir: Path) -> tuple[registry_mod.Registry, bool]:
    """Load the registry, tolerating there not being one yet.

    `add` has to work before a registry exists -- otherwise the first command a
    new user runs cannot succeed. A registry that exists but is broken still
    raises, so real errors are not mistaken for a fresh start.
    """
    if registry_dir.is_dir() and any(registry_dir.glob("*.json")):
        return registry_mod.load_registry(registry_dir), False
    empty = registry_mod.Registry(models={}, sets={}, aliases={}, files={})
    return empty, True


def cmd_add(args: argparse.Namespace) -> int:
    registry_dir = _reg_dir(args)
    registry, bootstrapping = _load_or_bootstrap(registry_dir)

    ref = args.repo
    if not ref:
        if not _interactive():
            raise SystemExit("vanity add: give a repo (org/name or URL)")
        print("vanity add -- onboard a model from Hugging Face")
        ref = _ask("Hugging Face repo (org/name or URL)")

    repo = onboard.parse_repo_ref(ref)

    # AGENTS.md rule: never add a repo that does not resolve.
    print(f"resolving {repo} ...")
    try:
        found = onboard.describe(repo)
    except FetchError as error:
        raise SystemExit(f"could not resolve {repo}: {error}") from error

    size_note = f"{found['file_count']} files"
    if found["total_bytes"]:
        size_note += f", {human_size(found['total_bytes'])}"
    print(f"  found: {size_note}, commit {found['revision'][:12]}")
    if found["gated"]:
        print("  note: this repo is gated -- accept its licence on Hugging Face "
              "first, and set HF_TOKEN before fetching")

    key = args.key or found["key"]
    runtime = args.runtime or found["runtime"]
    role = args.role or found["role"]
    size_hint = args.size_hint or found["size_hint"]
    revision = "main" if args.no_pin else found["revision"]
    if args.revision:
        revision = args.revision

    stems = sorted(registry.files)
    if bootstrapping:
        print(f"  no registry yet -- starting one in {registry_dir}")
    default_stem = args.file or ("models" if bootstrapping else
                                 (stems[0] if len(stems) == 1 else None))

    target_stem = default_stem
    if _interactive() and not args.yes:
        key = _ask("key", key)
        runtime = _ask("runtime", runtime)
        role = _ask("role", role)
        size_hint = _ask("size_hint", size_hint)
        revision = _ask('revision (a commit pins it; "main" tracks the branch)', revision)
        choices = f" ({', '.join(stems)})" if stems else ""
        target_stem = _ask(f"registry file{choices}", default_stem or "")

    if not target_stem:
        raise SystemExit(
            f"vanity add: choose a registry file with --file ({', '.join(stems)})"
        )

    # A mistyped stem would otherwise create a stray registry file silently.
    if stems and target_stem not in registry.files:
        if args.create:
            print(f"  creating a new registry file: {target_stem}.json")
        elif _interactive() and not args.yes:
            if _ask(f"{target_stem}.json does not exist -- create it? [y/N]", "n").lower() not in {"y", "yes"}:
                print("aborted")
                return 1
        else:
            raise SystemExit(
                f"vanity add: no registry file named {target_stem!r} "
                f"({', '.join(stems)}); pass --create to start a new one"
            )

    if key in registry.models:
        raise SystemExit(f"vanity add: {key!r} already exists in the registry")
    if not registry_mod.MODEL_KEY_RE.match(key):
        raise SystemExit(
            f"vanity add: {key!r} is not a valid key (lowercase letters, digits, . _ -)"
        )

    entry = {"repo": repo, "runtime": runtime, "role": role}
    if size_hint:
        entry["size_hint"] = size_hint
    if revision and revision != "main":
        entry["revision"] = revision

    target = registry_dir / f"{target_stem}.json"
    registry_dir.mkdir(parents=True, exist_ok=True)
    print()
    print(f"adding to {target.name}:")
    print(json.dumps({key: entry}, indent=2))

    if _interactive() and not args.yes and _ask("add this? [Y/n]", "y").lower() not in {"y", "yes"}:
        print("aborted")
        return 1

    backup = target.read_text() if target.exists() else None
    onboard.add_to_registry_file(target, key, entry)
    try:
        registry_mod.load_registry(registry_dir)
    except SystemExit as error:
        # Never leave the registry in a state vanity itself would reject.
        if backup is None:
            target.unlink(missing_ok=True)
        else:
            target.write_text(backup)
        raise SystemExit(f"vanity add: rejected, registry unchanged -- {error}") from error

    print(f"\nadded {key} to {target}")
    print(f"fetch it with: vanity fetch {key}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    registry_dir = _reg_dir(args)
    registry = registry_mod.load_registry(registry_dir)

    print(f"registry: {registry_dir}  "
          f"({len(registry.files)} files, {len(registry.models)} models)")
    print()

    for stem in sorted(registry.files):
        keys = registry.files[stem]
        description = registry.descriptions.get(stem)
        heading = f"{stem}  ({len(keys)} models)"
        print(heading if not description else f"{heading} -- {description}")
        for key in keys:
            model = registry.models[key]
            print(f"  {model.key:32} {model.size_hint:14} {model.repo}")
            print(f"  {'':32} {model.runtime}; {model.role}")
        print()

    if registry.sets:
        print("sets:")
        for sn in sorted(registry.sets):
            print(f"  {sn:32} {', '.join(registry.sets[sn])}")
        print()

    if registry.aliases:
        print("aliases:")
        for an in sorted(registry.aliases):
            print(f"  {an:32} -> {registry.aliases[an]}")
        print()

    print('targets: a model key, a set name, a file name above, or "all"')
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    registry = registry_mod.load_registry(_reg_dir(args))
    print(f"store: {config.store_root()}")
    state = state_mod.load_state()
    for model in registry.models.values():
        exists = model.path.exists()
        size = human_size(bytes_on_disk(model.path)) if exists else "-"
        entry = state.get(model.key, {})
        stale = bool(entry and entry.get("repo") != model.repo)
        status = "stale" if stale else entry.get("status") or ("present" if exists else "missing")
        if exists and not gitfetch.git_checkout_complete(model.path):
            status = "incomplete"
        if state_mod.lock_path(model).exists():
            status = "fetching"
        updated = entry.get("updated_at", "-") if not stale else "-"
        print(f"{model.key:32} {status:8} {size:10} {updated}")
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    checks = [
        ("git", bool(shutil.which("git")), "required for git-xet/git-lfs backends"),
        (
            "git-xet",
            any_command_ok((["git", "xet", "--version"], ["git", "xet", "version"], ["git-xet", "--version"])),
            "recommended Hugging Face large-file backend",
        ),
        ("git-lfs", command_ok(["git", "lfs", "version"]), "legacy/compatibility backend"),
        ("HF_TOKEN", bool(config.token()), "optional for public repos; raises rate limits, required for gated/private"),
    ]
    failed_required = False
    for name, ok, note in checks:
        print(f"{name:10} {'ok' if ok else 'missing'}  {note}")
        if name in {"git", "git-xet"} and not ok:
            failed_required = True
    print(f"store      {config.store_root()}")
    if failed_required:
        print("\nArch setup:")
        print("  sudo pacman -S --needed git git-lfs curl unzip")
        print("  curl --proto '=https' --tlsv1.2 -sSf https://raw.githubusercontent.com/huggingface/xet-core/refs/heads/main/git_xet/install.sh | sh")
        print("  git xet install")
        print("\nOr use --backend http / --backend git-lfs when Git-Xet is unavailable.")
        return 1
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    registry = registry_mod.load_registry(_reg_dir(args))
    models = registry_mod.expand_targets(args.targets, registry)
    jobs = getattr(args, "jobs", 1)
    if jobs == 1:
        for model in models:
            fetch_mod.fetch_one(model, backend=args.backend, retries=args.retries, min_interval=args.min_interval, quiet=args.quiet)
        return 0

    any_failed = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = {}
        for model in models:
            futures[executor.submit(
                fetch_mod.fetch_one, model, backend=args.backend,
                retries=args.retries, min_interval=args.min_interval,
                quiet=args.quiet,
            )] = model

        for future in concurrent.futures.as_completed(futures):
            model = futures[future]
            try:
                future.result()
            except FetchError:
                log(f"failed fetch {model.key}")
                any_failed = True

    if any_failed:
        return 1

    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    registry = registry_mod.load_registry(_reg_dir(args))
    failed = False
    state = state_mod.load_state()
    for model in registry_mod.expand_targets(args.targets, registry):
        path = model.path
        metadata = path / ".vanity.json"
        entry = state.get(model.key, {})
        state_ok = entry.get("status") == "fetched" and entry.get("repo") == model.repo
        metadata_ok = False
        if metadata.exists():
            try:
                metadata_ok = json.loads(metadata.read_text()).get("repo") == model.repo
            except json.JSONDecodeError:
                metadata_ok = False
        ok = path.exists() and gitfetch.git_checkout_complete(path) and (metadata_ok or state_ok)
        print(f"{model.key:32} {'ok' if ok else 'missing/incomplete'} {path}")
        failed = failed or not ok
    return 1 if failed else 0


def cmd_remove(args: argparse.Namespace) -> int:
    registry = registry_mod.load_registry(_reg_dir(args))
    for model in registry_mod.expand_targets(args.targets, registry):
        if not model.path.exists():
            print(f"missing {model.key}")
            continue
        if not args.yes:
            raise SystemExit(f"refusing to remove {model.path}; rerun with --yes")
        shutil.rmtree(model.path)
        state_mod.update_state(
            model.key,
            {
                "repo": model.repo,
                "status": "removed",
                "updated_at": now(),
            },
        )
        print(f"removed {model.key}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="vanity",
        description="vanity -- home-grown model library management from a local talent agent.",
    )
    root.add_argument(
        "--registry-dir", type=str, default=None,
        help="path to registry directory (default: $VANITY_REGISTRY_DIR, "
             "user config, ./registry, then the checkout)"
    )
    sub = root.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser("list", help="show known models, sets, and aliases")
    list_cmd.set_defaults(func=cmd_list)

    config_cmd = sub.add_parser("config", help="show or change where models and registry live")
    config_cmd.add_argument("--home", type=str, default=None, metavar="PATH",
                            help="set the one directory holding registry, library, and state")
    config_cmd.add_argument("--store", type=str, default=None, metavar="PATH",
                            help="set the model library location (overrides home/library)")
    config_cmd.add_argument("--registry-dir", dest="registry_dir_set", type=str,
                            default=None, metavar="PATH",
                            help="set the registry directory")
    config_cmd.set_defaults(func=cmd_config)

    add_cmd = sub.add_parser(
        "add",
        help="onboard a model from Hugging Face into the registry",
        description="Resolve a Hugging Face repo and add it to a registry file. "
                    "Run without arguments to be prompted for everything.",
    )
    add_cmd.add_argument("repo", nargs="?", default=None,
                         help="org/name or a Hugging Face URL; omit to be asked")
    add_cmd.add_argument("--file", default=None, metavar="STEM",
                         help="registry file to add to (e.g. serving)")
    add_cmd.add_argument("--key", default=None, help="registry key (default: derived from the repo name)")
    add_cmd.add_argument("--runtime", default=None, help="override the detected runtime")
    add_cmd.add_argument("--role", default=None, help="what this model is for")
    add_cmd.add_argument("--size-hint", dest="size_hint", default=None,
                         help="override the measured size")
    add_cmd.add_argument("--revision", default=None,
                         help="pin this commit/branch instead of the current HEAD")
    add_cmd.add_argument("--no-pin", action="store_true",
                         help="track main instead of pinning the current commit")
    add_cmd.add_argument("--create", action="store_true",
                         help="allow creating a registry file that does not exist yet")
    add_cmd.add_argument("--yes", action="store_true",
                         help="accept the derived values without prompting")
    add_cmd.set_defaults(func=cmd_add)

    status_cmd = sub.add_parser("status", help="show local fetch state")
    status_cmd.set_defaults(func=cmd_status)

    doctor_cmd = sub.add_parser("doctor", help="check local fetch tooling")
    doctor_cmd.set_defaults(func=cmd_doctor)

    fetch_cmd = sub.add_parser("fetch", help="fetch models, sets, or registry files")
    fetch_cmd.add_argument("targets", nargs="+", help="model keys, set names, file stems, or 'all'")
    fetch_cmd.add_argument("--backend", choices=("http", "git-xet", "git-lfs"), default="git-xet")
    fetch_cmd.add_argument("--retries", type=int, default=8)
    fetch_cmd.add_argument("--min-interval", type=float, default=2.0, help="seconds between fetch attempts")
    fetch_cmd.add_argument("--quiet", action="store_true")
    fetch_cmd.add_argument("--jobs", type=int, default=1, help="number of parallel fetch workers")
    fetch_cmd.set_defaults(func=cmd_fetch)

    verify_cmd = sub.add_parser("verify", help="check that local model directories exist")
    verify_cmd.add_argument("targets", nargs="*", default=["all"], help="model keys, set names, file stems, or 'all'")
    verify_cmd.set_defaults(func=cmd_verify)

    remove_cmd = sub.add_parser("remove", help="remove local model directories")
    remove_cmd.add_argument("targets", nargs="+", help="model keys, set names, file stems, or 'all'")
    remove_cmd.add_argument("--yes", action="store_true", help="confirm deletion")
    remove_cmd.set_defaults(func=cmd_remove)

    return root


def main() -> int:
    args = _build_parser().parse_args()
    try:
        ensure_store_configured(getattr(args, "command", ""))
        return args.func(args)
    except KeyboardInterrupt:
        log("interrupted")
        return 130
    except FetchError as error:
        log(f"error: {error}")
        return 1