# vanity

[releases](https://github.com/TGPSKI/vanity/releases) | [changelog](CHANGELOG.md) | [guide](docs/GUIDE.md) | [pate.sh](https://pate.sh)

**A declarative model library manifest, in one stdlib-only Python package.**

vanity maps stable names to `{repo, role, runtime, size_hint, revision}`, groups
them into sets, pins each to a commit, and fetches them over any of three
interchangeable backends.

No `pip install`. No compiled wheels. No Rust toolchain waiting to ambush you on
a new interpreter.

```bash
git clone git@github.com:TGPSKI/vanity.git
cd vanity

make doctor                  # check local tooling
vanity add BAAI/bge-m3       # onboard a model; starts a registry if none exists
vanity fetch bge-m3
```

## The manifest is the product

The byte-moving is the least interesting layer. What you keep is the manifest:

```json
{
  "description": "Primary runtime models and the persistent STT sidecar",
  "models": {
    "qwq-32b-awq": {
      "repo": "Qwen/QwQ-32B-AWQ",
      "size_hint": "~22GB",
      "runtime": "vLLM / AWQ",
      "role": "reasoning / TSS",
      "revision": "dc9f21221581580ccfa51b74077db6056b56cb69"
    }
  },
  "sets": { "reasoning-tss": ["qwq-32b-awq"] },
  "aliases": { "qwq": "qwq-32b-awq" }
}
```

That file is a few kilobytes of JSON you commit and review. Pinned to a commit,
it answers the question that's actually hard a year later: *which revision of
which repo is this, and can I get it again?* A model changing upstream becomes a
pull request instead of a silent behaviour change.

`registry/` is your data and is not tracked here. A small
[example](examples/registry/starter.json) ships instead.

## Zero dependencies, checked by a test

`pyproject.toml` declares `dependencies = []`, and
[`test_stdlib_only.py`](tests/test_stdlib_only.py) walks the package by AST and
fails the build if any import resolves outside `sys.stdlib_module_names`. The
claim is the product, so it isn't left to review discipline.

Scoped precisely, because the scoped version is the one that holds:

> vanity imports nothing but the Python standard library.
> The `http` backend needs **no external binaries at all** — it is the pure,
> zero-dependency path.
> `git-xet` and `git-lfs` are **optional system accelerators**: external
> binaries you shell out to (like `git` itself), never imported, never in the
> Python dependency graph.

This matters on the machine class vanity targets. `huggingface_hub` pulls in
pydantic, httpx, fsspec, filelock, and `hf_xet` — a compiled Rust wheel. On a
bleeding-edge interpreter you are one missing wheel away from a source build.
A model downloader that can drag you into a Rust compile is the wrong dependency
to accept.

## What it does

| | |
|---|---|
| **Registry** | `registry/*.json` — named keys to repo, role, runtime, size, revision. Validated on every load; a dangling reference is refused by name. |
| **Sets & aliases** | Fetch a named group as a unit. Alias a key for a short name, or to keep old scripts resolving after a rename. |
| **`vanity add`** | Onboard from Hugging Face — size, commit, and runtime derived from what the repo reports. Refuses repos that don't resolve. |
| **Provenance** | Pin every model to a commit. A fetch on another machine, or a year later, lands the same weights. |
| **Backends** | `http` (pure stdlib, no binaries), `git-xet` (default, chunk dedup), `git-lfs`. Same manifest either way. |
| **Fetch** | Resumable, atomic, `--jobs N` in parallel, per-model locks, backoff on rate limits. |
| **State** | Atomic writes, stale-lock breaking via PID liveness, a `.vanity.json` sidecar in every model directory. |

## Add a model

```bash
$ vanity add Qwen/QwQ-32B-AWQ
resolving Qwen/QwQ-32B-AWQ ...
  found: 19 files, 18.0GB, commit dc9f21221581
  key [qwq-32b-awq]:
  runtime [vLLM / AWQ]:
  role [text generation]: reasoning challenger
  size_hint [~18.0GB]:
  revision (a commit pins it; "main" tracks the branch) [dc9f2122158158...]:
  registry file (challengers, serving, support): challengers
```

Size is summed from the file tree, the revision is the repo's current commit,
and the runtime is inferred from the repo's own tags — AWQ/NVFP4/FP8/GPTQ, GGUF,
sentence-transformers, CTranslate2. Every value is a prompt with that default,
so accept the lot or correct a line. `--yes` takes them all.

## Install

Cloning is enough — the Makefile runs the package straight from `src/`:

```bash
git clone git@github.com:TGPSKI/vanity.git
cd vanity
make list
```

For a `vanity` command on your PATH:

```bash
make install        # pip install -e .
```

**Verify the install** — no model download required:

```bash
make doctor                                        # tooling + resolved store
VANITY_REGISTRY_DIR=examples/registry vanity list   # the shipped example
make test                                          # 117 tests, no network
```

## Configure

One home backs the manifests, the weights, and the state, so the normal setup is
a single path:

```
<home>/
  registry/   your manifests -- small, versioned, diffable
  library/    the weights    -- large, gitignored, often on another mount
  .state/     fetch state and locks
```

```bash
vanity config --home ~/vanity        # the usual case
vanity config --store /mnt/models    # only when weights belong on another disk
vanity config                        # show what resolved, and from where
```

The first command that touches your library asks once and remembers. It only
prompts on a terminal — scripts and CI take the default rather than blocking.

## Go deeper

| you want to… | start here | then |
|---|---|---|
| **use** it — install, config, registry format, pinning, backends | [docs/GUIDE.md](docs/GUIDE.md) | [command reference](docs/GUIDE.md#command-reference) · [troubleshooting](docs/GUIDE.md#troubleshooting) |
| **understand** the internals | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — module map, fetch loop, locking | [CHANGELOG.md](CHANGELOG.md) — every fix and why it mattered |
| **contribute** (human or agent) | [AGENTS.md](AGENTS.md) — selection and verification rules | [CONTRIBUTING.md](CONTRIBUTING.md) — the invariants that regress quietly |
| **assess** the security posture | [SECURITY.md](SECURITY.md) — trust model, and what a pin does *not* give you | |

## License

GPL-3.0-only. See [LICENSE](LICENSE).
