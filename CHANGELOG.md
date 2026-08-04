# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.1] - 2026-08-03

### Changed

- **Tagline.** "Home-grown model library management from a local talent agent"
  is now the hero line across the README, the guide, the package metadata, and
  `vanity --help`.
- **`main` no longer requires an approving review.** On a solo-maintained repo a
  1-approval rule means every change needs `gh pr merge --admin`, which trains
  you to reach for the override reflexively — that erodes the protection more
  than lowering the count does. Pull requests, status checks, required
  signatures, and no-force-push all stay; code-owner review stays configured so
  it takes effect the moment a second maintainer exists.

### Fixed

- **Test suite portability on Windows.** Two path assertions compared against
  POSIX separators, and three cases stub `git` with a `#!/bin/sh` script that
  Windows cannot execute. The path assertions now render through `Path`; the
  stub-based cases are skipped there with the reason stated, since the argv
  construction they cover is platform-independent. Product code was not at
  fault — this was the Windows CI tier doing its job on its first run.

## [0.2.0] - 2026-08-03

First public release. v0.1.0 was a working personal tool: one 980-line
`scripts/modelctl.py` with the library hardcoded as Python literals. This
release makes it something someone else can use — the library became data, the
dependency claim became a test, and several things that looked like they worked
turned out not to.

### Added

- **`vanity add <repo>` onboards a model from Hugging Face.** Derives the fields
  from what the repo reports rather than what you remember: size summed from the
  file tree, revision from its current commit, runtime inferred from tags and
  `library_name` (AWQ/NVFP4/FP8/GPTQ, GGUF, sentence-transformers, CTranslate2,
  encoder pipelines). Accepts `org/name`, a URL, or a `/tree/<rev>` link.
  Interactively every value is a prompt with that default; `--yes` takes them
  all. It refuses a repo that does not resolve — the `git ls-remote`
  verification rule from `AGENTS.md`, enforced rather than remembered — and
  rolls back the write if the result would not load.
- **`vanity config`** shows where the registry, library, and state resolve to
  *and the source of each value*, which is the fastest answer to "why is it
  looking there?". `--home`, `--store`, and `--registry-dir` set them.
- **Commit-SHA revision pinning.** A registry entry stops describing *a* model
  and starts describing exactly the bytes you have. Pinned by default for new
  entries.
- **`--jobs N` for parallel fetches.** Per-model locks isolate them; writes to
  the shared state file are serialised. A single model's failure does not abort
  its siblings.
- **A first-run prompt** for the library location, saved to
  `~/.config/vanity/config.json` and never asked again. Guarded on both stdin
  and stdout being terminals, so scripts and CI take the default rather than
  blocking on input.
- **A registry JSON schema** (`schemas/modelset.schema.json`) and a shipped
  example registry (`examples/registry/starter.json`) — three small models, ~5GB,
  exercising `description`/`sets`/`aliases` so it doubles as a format reference.
- **Documentation**: `docs/GUIDE.md` and `docs/ARCHITECTURE.md`, plus
  `CONTRIBUTING.md`, `SECURITY.md`, and this changelog.
- **CI** on Python 3.10 and 3.14, with a full-scope tier gated by the
  `full-test` label.
- **GPL-3.0-only licence**, declared in `pyproject.toml` via PEP 639.

### Changed

- **The registry moved from Python literals to `registry/*.json`.** ~200 lines of
  hardcoded `MODELS`/`GROUPS`/`PROFILES`/`ALIASES` became data: diffable,
  reviewable, editable by tools, and safe for an agent to modify. This was the
  gate on the project being usable by anyone else.
- **The library manifest is the operator's data, not part of the tool.**
  `registry/` is no longer tracked by git. A clone previously shipped 26 models
  describing one machine, so a new user's `vanity list` showed someone else's
  library and `vanity fetch all` would have attempted roughly 700GB.
- **One home directory backs registry, library, and state.** Previously each
  resolved independently, and state was derived from the package location — so a
  non-editable install would have written state into its own site-packages
  directory. `$VANITY_HOME` → user config → a clone-shaped cwd →
  `~/.local/share/vanity`.
- **The project is `vanity` throughout** — package, CLI, and environment
  variables (`VANITY_*`). It was `modelctl` internally and `models` in the
  README.
- **`modelctl.py` became the `src/vanity/` package.** The pitch is stdlib-only,
  not single-file; `dependencies = []` and running from a clone both still hold.
- **Registry files are named for what they hold** — `serving`, `support`,
  `challengers`, replacing `group-one/two/three`. Groups were removed as a
  concept when targeting moved to file stems; the names lingered and read as a
  feature that no longer existed.
- **`vanity list` says what it is showing**: the registry path with file and
  model counts, each file labelled with its `description` (previously parsed and
  discarded), and the alias table — which was not visible from the CLI at all.
- **Set members are validated after every file is read**, so a set may reference
  a model declared in any registry file. Validation ran inside the per-file loop,
  so cross-file references failed depending on filename order.
- **`doctor` no longer implies `HF_TOKEN` is only for gated repos.** Public
  downloads work anonymously — verified. A token mainly raises rate limits.

### Fixed

- **The `http` backend could not fetch any repository.** `repo_tree` built the
  tree URL with `quote(repo, safe="")`, percent-encoding the slash in
  `org/repo`; the API answers `400 Invalid repo name: ... includes an
  url-encoded slash`. This is the pure-stdlib path the zero-dependency claim
  rests on, and it had never worked.
- **The `http` backend crashed when the library sat outside the checkout.**
  Progress lines used `Path.relative_to(ROOT)`, which raises `ValueError` — the
  normal case once a store is configured.
- **Transient git failures never retried.** `run_git_command` raised
  `CalledProcessError` with no stderr, and the retry check inspected
  `str(error)`, which can never contain `429` or `rate limit`. The backoff
  machinery read as a safety property while being dead code. Git stderr is now
  captured into a `GitFailure` carrying the returncode, argv, and output.
- **A `SIGKILL`ed fetch wedged a model permanently.** The lock file recorded a
  PID that was never read back, so the model reported `fetching` forever until
  someone deleted the file by hand. Locks are now broken when the recorded PID
  is dead, refused when it is alive, and refused when unreadable rather than
  guessed. Liveness is portable: Windows has no signal-0 probe, so the process
  handle is queried instead.
- **A pinned commit could not be fetched by the git backends.**
  `git clone --branch` accepts branch and tag names only and fails on a raw SHA
  with "Remote branch ... not found in upstream origin". Pinned revisions now
  clone the default branch and check out the commit detached; an existing pinned
  checkout fetches and re-detaches rather than running `pull --ff-only` against
  a detached HEAD.
- **`vanity add` could not run before a registry existed**, which made the first
  command a new user could run impossible. It now starts one.
- **A mistyped `--file` silently created a new registry file.** `--file servng`
  wrote `servng.json` and reported success. Creating a file is now explicit.
- **The documented registry format could not be loaded.** The README described
  `hf_repo`/`hf_file`/`model_type`/`arch_bits`/`quant`/`notes` and top-level
  `profiles`/`groups` — every one rejected by the loader, so a new user's first
  copy-paste failed. `tests/test_readme_example.py` now runs the README's own
  example through the real loader.
- **`VANITY_REGISTRY_DIR` was documented but never read.**
- **The test suite wrote into the real library and state.** A test built a temp
  store and then never used it, leaving a fake model in `library/` and an entry
  in `fetches.json`. Tests were also coupled to the personal registry, so editing
  your own library broke the suite — which would have hit every fork.
- **`bytes_on_disk` ran twice per successful fetch** — two full walks of a 296GB
  tree for one log line.
- **The `qwen3-coder-next-fp8` key contradicted its own description** (a BF16
  base checkout). Renamed with a back-compat alias. Its size hint said `~14GB`
  against 89.9GB on disk.
- **A stray `mode + ""` and a `SyntaxError`** from the v0.1.0 file.

### Removed

- **`hack/`** — vLLM warmup and benchmarking scripts, unrelated to managing a
  model library and carrying the only third-party `requirements.txt` in a repo
  whose headline claim is that it has none.
- The `fetch-group-*` Makefile targets, superseded by `TARGET=`.

### Security

- **The dependency claim is enforced mechanically.**
  `tests/test_stdlib_only.py` walks `src/vanity/` by AST and fails the build on
  any import outside `sys.stdlib_module_names`. There is no third-party package
  to compromise and no lockfile to audit.
- **CI actions are pinned to commit SHAs**, so a moved tag cannot change what
  runs in the build.
- Repository rulesets require signed commits, protect `main`, and make release
  tags immutable.
- `SECURITY.md` documents the trust model and states plainly what a pin does
  *not* give you: it names a commit, not a checksum, and does not survive an
  upstream force-push.

## [0.1.0] - 2026-06-26

Initial working tool, personal use only.

- Single-file `scripts/modelctl.py` — pure stdlib, ~980 lines.
- Models, groups, profiles, and aliases as Python literals in the source.
- Three fetch backends: `http`, `git-xet`, `git-lfs`.
- Atomic fetch state, per-model lock files, HTTP range resume, retry with
  exponential backoff, progress heartbeats.
- Makefile interface and `AGENTS.md` with repo-verification and grouping rules.

[Unreleased]: https://github.com/TGPSKI/vanity/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/TGPSKI/vanity/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/TGPSKI/vanity/releases/tag/v0.2.0
[0.1.0]: https://github.com/TGPSKI/vanity/releases/tag/v0.1.0
