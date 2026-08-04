# vanity guide

> Home-grown model library management from a local talent agent.

Everything you do with vanity, in the order you'll do it: set up, declare a
library, fetch it, keep it honest.

For internals see [ARCHITECTURE.md](ARCHITECTURE.md).

- [Install](#install)
- [Configuration](#configuration)
- [The registry](#the-registry)
- [Adding models](#adding-models)
- [Fetching](#fetching)
- [Provenance](#provenance)
- [Backends](#backends)
- [Command reference](#command-reference)
- [Troubleshooting](#troubleshooting)

---

## Install

Python 3.10+. Nothing else, for the `http` backend. `git` plus `git-xet` or
`git-lfs` if you want the faster git backends.

Cloning is enough — the Makefile runs the package straight from `src/`:

```bash
git clone git@github.com:TGPSKI/vanity.git
cd vanity
make doctor
```

```
git        ok  required for git-xet/git-lfs backends
git-xet    ok  recommended Hugging Face large-file backend
git-lfs    ok  legacy/compatibility backend
HF_TOKEN   ok  optional for public repos; raises rate limits, required for gated/private
store      /home/you/vanity/library
```

`doctor` exits non-zero if `git` or `git-xet` is missing and prints the install
commands. Neither is fatal — `--backend http` needs no external binaries.

To get `vanity` on your PATH instead of typing `make`:

```bash
make install        # pip install -e .
vanity list
```

Both are the same program. This guide uses `vanity`; every command has a `make`
equivalent, listed by `make help`.

### About HF_TOKEN

A token is **not** required to download public models — public repos clone and
download anonymously. What a token buys:

- **Higher rate limits.** Anonymous traffic is throttled hardest, which bites
  when pulling many files or several models at once.
- **Gated and private repos.** Anything behind a licence click or an org
  membership. No amount of retrying substitutes for it.

Put it in `.env` (gitignored, and the Makefile exports it) so it stays out of
your shell history:

```bash
cp example.env .env
$EDITOR .env
```

---

## Configuration

vanity needs three paths: manifests, weights, state. They default to one home,
so a normal setup configures exactly one thing.

```
<home>/
  registry/   your manifests -- small, versioned, diffable
  library/    the weights    -- large, gitignored, often on another mount
  .state/     fetch state and locks
```

**Why registry and library stay separate.** `registry/` is a few kilobytes of
JSON you commit and review — it's the artifact. `library/` is hundreds of
gigabytes you never commit and often want on a different disk. Defaulting them
into one home means one path to configure; keeping them separable means the
weights can live on the big disk without dragging the manifests off your SSD.

### First run

The first command that touches your library asks once:

```
vanity: first run -- where should the model library live?
  Enter to accept the default: /home/you/vanity/library
  model library path:
```

The answer lands in `~/.config/vanity/config.json` and is never asked again. The
prompt only appears on an interactive terminal — scripts and CI take the default
rather than blocking on input.

To decide up front:

```bash
vanity config --home ~/vanity        # the usual case: one path
vanity config --store /mnt/models    # only if weights belong on another disk
vanity config                        # show what resolved, and from where
```

```
config file  /home/you/.config/vanity/config.json
home         /home/you/vanity  (user config)
store        /mnt/models  (env MODEL_STORE)
registry     /home/you/vanity/registry  (default)
state        /home/you/vanity/.state
```

That `(source)` column is the fastest answer to "why is it looking *there*?".

### Resolution order

**home** — `$VANITY_HOME` → user config → the current directory if it looks like
a checkout → `~/.local/share/vanity`.

**library** — `$MODEL_STORE` or `$MODELS_DIR` → user config → `<home>/library`.

**registry** — `--registry-dir` → `$VANITY_REGISTRY_DIR` → user config →
`<home>/registry`.

State always follows home: `<home>/.state`. It is deliberately not derived from
the package location, so an installed vanity never writes into its own
site-packages directory.

Environment beats saved config, which is what lets one install drive several
libraries:

```bash
MODEL_STORE=/mnt/models vanity status
VANITY_REGISTRY_DIR=~/other-registry vanity list
```

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `VANITY_HOME` | checkout, else `~/.local/share/vanity` | one directory holding registry, library, and state |
| `MODEL_STORE` | `<home>/library` | root directory for fetched models |
| `MODELS_DIR` | — | synonym for `MODEL_STORE` |
| `VANITY_REGISTRY_DIR` | `<home>/registry` | directory holding registry JSON files |
| `VANITY_HEARTBEAT_SECONDS` | `60` | seconds between progress lines (minimum 5) |
| `HF_TOKEN` | — | Hugging Face token |
| `HUGGING_FACE_HUB_TOKEN` | — | synonym for `HF_TOKEN` |
| `XDG_CONFIG_HOME` | `~/.config` | where the user config lives |
| `XDG_DATA_HOME` | `~/.local/share` | fallback home for an installed vanity |

A `.env` at the repo root is read for all of the above.

---

## The registry

A directory of JSON files. Each declares models, and optionally sets and
aliases. Together they're your library manifest.

```json
{
  "$schema": "../schemas/modelset.schema.json",
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
  "sets": {
    "reasoning-tss": ["qwq-32b-awq"]
  },
  "aliases": {
    "qwq": "qwq-32b-awq"
  }
}
```

Top-level keys are `$schema`, `description`, `models`, `sets`, `aliases`.
Anything else is rejected by name at load time. Fill in `description` — `vanity
list` prints it as the file's heading, which is the difference between a
labelled section and a bare filename.

Nothing is special about filenames. A file is a grouping you chose, its stem is
a fetchable target, and one file is fine.

### Model fields

| Field | Required | Meaning |
|---|---|---|
| `repo` | yes | Hugging Face repo as `org/name` |
| `runtime` | yes | how you intend to serve it — free text, e.g. `vLLM / AWQ` |
| `role` | yes | what it's for, in your setup — free text |
| `size_hint` | no | human-readable size, shown by `list`; never enforced |
| `revision` | no | commit, branch, or tag. Defaults to `main` |

`runtime` and `role` are deliberately free text — notes to your future self
about why this model is in the library and how you plan to run it. vanity never
parses them.

Keys must match `^[a-z0-9][a-z0-9._-]*$`. The key is also the directory name
under the library, so renaming one is a migration — see
[Renaming a key](#renaming-a-key).

### Sets and aliases

A **set** is a named list of keys fetched as a unit. Sets may reference models
declared in any registry file, not only their own.

An **alias** is a second name for a key: a short name you'd rather type, or a
compatibility shim after a rename so old scripts keep resolving. Aliases may
chain; cycles are detected.

### Targets

Anywhere a target is accepted it resolves in this order: model key → alias → set
name → registry file stem → the literal `all`. `vanity list` ends by restating
exactly that.

### Validation

The registry is validated on every load, and vanity refuses to operate on one it
doesn't fully understand. It fails loudly, naming the offending key:

- unknown top-level keys, or unknown fields on a model
- missing `repo`, `runtime`, or `role`; a `repo` that isn't `org/name`
- a key breaking the key pattern, or declared in two files
- a set referencing a model that doesn't exist anywhere
- a set colliding with a registry file name, or a duplicate set name
- an alias shadowing an existing name, pointing at nothing, or forming a cycle

A manifest that tolerates a dangling reference will eventually fetch the wrong
thing. `schemas/modelset.schema.json` describes the same rules for editors; the
loader is the authority.

### What ships in this repo

`registry/` is not tracked by git — your library manifest is your data, not part
of the tool. `examples/registry/starter.json` ships instead: three small models
with sets and aliases, usable as a template.

```bash
VANITY_REGISTRY_DIR=examples/registry vanity list
mkdir -p registry && cp examples/registry/starter.json registry/
```

---

## Adding models

```bash
vanity add Qwen/QwQ-32B-AWQ
```

```
resolving Qwen/QwQ-32B-AWQ ...
  found: 19 files, 18.0GB, commit dc9f21221581
  key [qwq-32b-awq]:
  runtime [vLLM / AWQ]:
  role [text generation]: reasoning challenger
  size_hint [~18.0GB]:
  revision (a commit pins it; "main" tracks the branch) [dc9f2122158158...]:
  registry file (challengers, serving, support): challengers
```

Accepts `org/name`, a full URL, `hf.co/...`, or a link with `/tree/<rev>` on the
end. Every field is derived from what the repo reports — size summed from the
file tree, revision from its current commit, runtime inferred from its tags —
and every one is a prompt with that default, so accept the lot or fix a line.

```bash
vanity add                                        # prompts for everything
vanity add BAAI/bge-m3 --file support --yes       # scripted
vanity add org/model --no-pin                     # track main instead of pinning
vanity add org/model --file experiments --create  # start a new registry file
```

Behaviour worth knowing:

- **It refuses a repo that doesn't resolve.** That's the `git ls-remote`
  verification rule from [AGENTS.md](../AGENTS.md), enforced rather than
  remembered.
- **It works before a registry exists**, creating one — otherwise the first
  command a new user runs couldn't succeed.
- **A `--file` that doesn't match an existing file is refused** unless
  `--create`, so a typo can't quietly scatter files into the registry.
- **If the result wouldn't load, the write is rolled back** and the registry is
  left exactly as it was.
- **Gated repos are flagged, not refused.** Their metadata is public even when
  downloads aren't, so you can pin one before you can fetch it.

Hand-editing is equally supported and sometimes better — you're writing `role`
and `runtime` anyway, and a text editor is the right tool for reorganising sets.
Run `vanity list` afterwards; a validation error is cheaper than a bad fetch.

---

## Fetching

```bash
vanity fetch bge-m3                  # one model
vanity fetch default-coding          # a set
vanity fetch serving                 # every model in one registry file
vanity fetch all --jobs 4            # everything, four at a time
```

Fetches are resumable and safe to re-run: a complete model is skipped, an
interrupted one resumes, and a per-model lock stops two fetches colliding. With
`--jobs N`, one model's failure doesn't abort its siblings; the command exits
non-zero if any failed.

Long fetches belong in the background:

```bash
mkdir -p logs
make fetch-bg TARGET=all
tail -f logs/fetch-all.log
```

Then check what you have:

```bash
vanity status      # state of every model in the registry
vanity verify      # confirm what's on disk is complete and matches
```

---

## Provenance

An entry without a `revision` says "fetch whatever `main` points at today". Pin
it and the entry stops describing *a* model and starts describing *exactly the
bytes you have*.

```json
"qwq-32b-awq": {
  "repo": "Qwen/QwQ-32B-AWQ",
  "revision": "dc9f21221581580ccfa51b74077db6056b56cb69"
}
```

This is the durable argument for a manifest layer. Anyone can download a model;
answering "which revision of which repo is this, and can I get it again" is the
part that's hard a year later.

**What a pin gives you:** reproducibility across machines and across time; a
diffable record, so a model changing upstream is a pull request rather than a
silent behaviour change; and a real answer when something starts behaving
differently and the first question is whether the weights moved.

**What it does not:**

- **It is not a checksum.** It names a git commit. It does not verify the bytes
  you received hash to anything expected.
- **It does not survive a force-push or a deletion.** If upstream rewrites
  history the pin becomes unfetchable. It still records what you *had*, which is
  worth something — but it is not an archive. If a model matters, keep a copy.
- **It says nothing about the licence.** Pinning a gated model doesn't grant
  access to it.

`vanity add` pins by default. To pin an existing entry, look up the commit:

```bash
git ls-remote https://huggingface.co/Qwen/QwQ-32B-AWQ HEAD
```

For a model already on disk, pin the commit **you actually have**, not current
upstream HEAD — otherwise the manifest claims a provenance the disk doesn't
have:

```bash
git -C <library>/<key> rev-parse HEAD
```

Gated repos return `403` from `git ls-remote` but serve metadata publicly, which
is why `vanity add` can pin one you can't yet download.

A pinned checkout sits on a detached HEAD. That's expected: a pin checks out an
exact commit. Re-fetching fetches and re-detaches rather than pulling.

Each fetched model also carries a `.vanity.json` sidecar recording key, repo,
revision, and fetch time — so a directory copied elsewhere still says what it is.

---

## Backends

Three interchangeable transports. The manifest is identical either way.

| Backend | External binaries | Best at |
|---|---|---|
| `git-xet` (default) | `git`, `git-xet` | large model repos on Hugging Face |
| `git-lfs` | `git`, `git-lfs` | older repos, or machines already using LFS |
| `http` | **none** | anywhere, and when you want zero moving parts |

**Where the dependency claim lands.** vanity imports nothing but the standard
library in all three cases. The difference is what it *shells out to*: `http`
needs no external binaries at all, and is the path the zero-dependency claim
rests on. `git-xet` and `git-lfs` are optional system accelerators — external
binaries invoked as subprocesses, exactly like `git` itself, never imported and
never in the Python dependency graph.

That distinction is worth being precise about. "Zero dependencies" would be
misleading if the default path silently required a Rust binary. It doesn't
require one *in Python* — but it does require one *on the system*. Choose `http`
when that matters.

**git-xet** is Hugging Face's current large-file transport with chunk-level
dedup, and the fastest for the big safetensors repos vanity is usually pointed
at. Install:

```bash
curl --proto '=https' --tlsv1.2 -sSf \
  https://raw.githubusercontent.com/huggingface/xet-core/refs/heads/main/git_xet/install.sh | sh
git xet install
```

**http** is the pure path: `urllib`, a tree listing, per-file downloads with
`Range`-based resume, and a size check before `.part` moves into place. A server
that ignores the range and replies `200` restarts that file cleanly rather than
corrupting it. Slower than `git-xet` on large repos — no chunk dedup, no
parallel streams within a file. That's the trade.

**Retries.** All backends retry transient failures — rate limits, timeouts,
connection resets, `5xx` — with exponential backoff, jitter, and `Retry-After`
when the server sends it, up to `--retries` (default 8). A genuine error (repo
doesn't exist, permission denied) fails immediately rather than after eight
backoffs.

**Parallelism** is across models, not within a file. Four jobs on four models
helps; four jobs on one model does nothing. On a slow link more jobs mostly
means more ways to hit rate limits — start with 4.

---

## Command reference

```
vanity [--registry-dir PATH] <command> [options]
```

`--registry-dir` is global, so it goes **before** the subcommand:

```bash
vanity --registry-dir examples/registry list    # correct
vanity list --registry-dir examples/registry    # error
```

### list

Print the registry: every file with its description and models, then sets,
aliases, and what a target may be. Reads the registry only — never touches the
library, never triggers the first-run prompt.

### add

Onboard a model from Hugging Face. See [Adding models](#adding-models).

| Flag | Effect |
|---|---|
| `--file STEM` | registry file to add to |
| `--key KEY` | registry key (default: derived from the repo name) |
| `--runtime TEXT` | override the inferred runtime |
| `--role TEXT` | what this model is for |
| `--size-hint TEXT` | override the measured size |
| `--revision REV` | pin this commit/branch instead of current HEAD |
| `--no-pin` | track `main` instead of pinning |
| `--create` | allow creating a registry file that doesn't exist yet |
| `--yes` | accept derived values without prompting |

### config

Show or set where things live: `--home`, `--store`, `--registry-dir`. See
[Configuration](#configuration).

### status

State of every model, and the store it resolved to.

| State | Meaning |
|---|---|
| `fetched` | recorded complete by a successful fetch |
| `missing` | no directory on disk |
| `incomplete` | present, but the checkout has unhydrated or modified files |
| `fetching` | a lock file exists for this model |
| `stale` | state recorded against a different repo than the registry now names |
| `failed` | last fetch failed; the error is in the state file |
| `present` | directory exists but vanity has no state for it |

Two caveats: `fetching` reflects a lock file, not a live process (see
[Troubleshooting](#a-model-is-stuck-in-fetching)), and sizes come from walking
each directory, so on a large library `status` takes a moment.

### doctor

Check local tooling and report the store. Exits non-zero if `git` or `git-xet`
is missing, printing install commands.

### fetch

| Flag | Default | Effect |
|---|---|---|
| `--backend {http,git-xet,git-lfs}` | `git-xet` | transport |
| `--jobs N` | `1` | models fetched in parallel |
| `--retries N` | `8` | retry attempts for transient failures |
| `--min-interval SECONDS` | `2.0` | minimum gap between attempts |
| `--quiet` | off | suppress progress heartbeats |

### verify

Check that what's on disk is complete and matches the registry. A model is `ok`
when its directory exists, the checkout has no missing or unhydrated files, and
either its sidecar or the state file agrees with the registry's repo. Exits
non-zero if any target fails, which makes it usable as a health check.

### remove

Delete a model's directory. Requires `--yes`. Refuses `TARGET=all` outright —
removing an entire library shouldn't be one flag away. The registry entry is
untouched; only the bytes go.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | success |
| `1` | fetch or verification failed, operation declined, or a validation error |
| `130` | interrupted (Ctrl-C) |

---

## Troubleshooting

### `no registry yet at <path>`

No registry where vanity looked. Either you haven't made one, or it's elsewhere.

```bash
vanity add <org>/<repo>          # start one here
vanity config --home ~/vanity    # or point at where yours lives
vanity config                    # show what resolved, and from where
```

### vanity is looking in the wrong place

Environment beats saved config, so a stale `MODEL_STORE` or
`VANITY_REGISTRY_DIR` in your shell wins over `vanity config`. The `config`
output names the source of each value. Check `.env` too — it's read for the same
variables.

### A model is stuck in `fetching`

`status` reports `fetching` when a lock file exists, not when a process is
running. A fetch killed with `SIGKILL` leaves its lock behind.

This heals itself: the next fetch checks whether the recorded PID is alive and
breaks the lock if it isn't, logging that it did. To clear it now, delete
`<home>/.state/<key>.lock`. A lock held by a *live* process is refused rather
than broken — two fetches into one directory would corrupt it.

### A model shows `incomplete`

The directory exists but the checkout has missing, modified, or unhydrated files
— commonly LFS/Xet pointer files never replaced with content. Re-run the fetch;
it resumes and re-hydrates from `HEAD`. If it stays `incomplete`, check for
`GIT_LFS_SKIP_SMUDGE=1` in the environment and confirm `git-xet`/`git-lfs` is
installed (`vanity doctor`).

### A model shows `stale`

State records this key against a different repo than the registry now names —
usually because you edited `repo` after fetching. The bytes on disk came from
the old repo:

```bash
vanity remove <key> --yes && vanity fetch <key>
```

### `403` on a gated repo

Accept the licence on the model's Hugging Face page, then set `HF_TOKEN`. Being
able to `vanity add` a gated repo doesn't mean you can fetch it — metadata is
public, downloads aren't.

### `401` on a public repo

An invalid or expired `HF_TOKEN` can fail requests for repos that need no token
at all. The git backends retry once without it; the clean fix is to correct or
unset it: `HF_TOKEN= vanity fetch <key>`.

### Rate limits and `429`

Backoff is automatic. If it keeps happening: set `HF_TOKEN` (free, and anonymous
traffic is throttled hardest), lower `--jobs`, or raise `--min-interval`.

### Fetch fails immediately without retrying

Deliberate. Only transient failures retry. A repo that doesn't exist, a
permission failure, or a non-empty target directory fails at once rather than
after eight backoffs. The message names which.

### `exists and is not an empty git checkout`

The target directory has content but isn't a git checkout vanity can update —
often a manually copied model. Move it aside, or remove it and re-fetch.

### Renaming a key

The library path is `<store>/<key>` with no indirection, so a rename points
vanity at a directory that doesn't exist and a fetch would re-download. To
rename without re-downloading:

1. rename the key in the registry
2. add an alias from the old key to the new one
3. rename the directory in the library to match
4. update the `key` field in `<model>/.vanity.json`
5. rekey the entry in `<home>/.state/fetches.json`, including its `path`

`vanity status` should then show the model `fetched` with its original
timestamp, and `vanity verify <key>` should report `ok`. If it reports
`missing/incomplete`, stop — something didn't line up, and fetching will start a
fresh download.

### Everything looks wrong after moving things

Reconcile in this order:

```bash
vanity config      # where does vanity think everything is?
vanity list        # does the registry load?
vanity status      # what does it think is on disk?
vanity verify      # does the disk agree?
```

Most confusion is a store or registry resolving somewhere unexpected, and
`config` shows that in one line.
