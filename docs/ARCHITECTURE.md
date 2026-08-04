# Architecture

About 1,900 lines of Python across ten modules, importing only the standard
library. Small enough to read in a sitting, which is deliberate: a tool whose
pitch is "you can audit this" has to stay auditable.

## Module map

| Module | Lines | Responsibility |
|---|---:|---|
| `cli.py` | ~500 | argument parsing and command implementations |
| `registry.py` | ~260 | parse registry files, validate, resolve targets |
| `onboard.py` | ~220 | resolve a HF repo and derive registry fields (`add`) |
| `gitfetch.py` | ~200 | git-xet and git-lfs backends |
| `httpfetch.py` | ~170 | pure-stdlib HTTP backend |
| `config.py` | ~160 | where registry, library, and state resolve from |
| `util.py` | ~155 | errors, logging, sizes, process/command helpers |
| `state.py` | ~110 | fetch state and per-model locking |
| `fetch.py` | ~95 | retry loop and backend dispatch |

The dependency direction is one-way: `cli` uses everything; `fetch` dispatches
to the backends; `registry`, `config`, `state`, and `util` do not import
upward. `util` imports nothing from the package except lazily inside a function,
which keeps it loadable on its own.

## Data model

A `Model` is a frozen dataclass — `key`, `repo`, `size_hint`, `runtime`, `role`,
`revision` — with one computed property:

```python
@property
def path(self) -> Path:
    return store_root() / self.key
```

The library path derives from the key with no indirection. That keeps the
mapping obvious (a directory is named for its key) at the cost of making a key
rename a migration; see [Renaming a key](GUIDE.md#renaming-a-key).

`store_root()` is resolved at access time rather than captured at construction,
so a `Model` stays correct when configuration changes underneath it — which is
what makes tests able to redirect the store without rebuilding fixtures.

A `Registry` is frozen too: models, sets, aliases, per-file key lists, and
per-file descriptions.

## Loading and validation

`load_registry()` reads every `*.json` in the directory in sorted order, then
validates in two passes:

1. **Per file** — unknown top-level keys, unknown model fields, missing required
   fields, malformed repos, bad key patterns, duplicate model keys across files.
2. **Across all files** — set members, set/file name collisions, alias shadowing,
   alias targets, alias cycles.

The second pass exists because validation used to run inside the per-file loop,
which meant a set could only reference models declared in the same or an earlier
file. Files are read in sorted order, so that failed silently depending on
filename.

Every failure raises `SystemExit` naming the offending key. The registry is
never partially loaded: vanity refuses to operate on a manifest it does not
fully understand.

## Configuration resolution

One function, `home()`, backs registry, library, and state. Each has its own
override, but all three default to a subdirectory of home.

The important property is that state is **not** derived from the package
location. `ROOT` (the checkout the package was imported from) is only a
fallback for finding a registry when you are working inside a clone. A
non-editable install must never write state into its own site-packages
directory, which is exactly what a `__file__`-derived state path would do.

## The fetch loop

`fetch_one()` owns retries and state; the backends own transport.

```
acquire per-model lock
  for attempt in 1..retries+1:
      run_download(...)          -> backend
      on transient failure       -> sleep with backoff, retry
      on fatal failure           -> record failure, raise
      on success                 -> record state, write sidecar, return
release lock
```

"Transient" is decided from the actual error: HTTP status for the http backend,
git's captured stderr for the git backends. This matters — an earlier version
inspected `str(CalledProcessError)`, which never contains a status code, so the
git backends' retry machinery was dead code that looked alive. Capturing stderr
is what makes the retry real.

Backoff is exponential with jitter, capped, and honours `Retry-After` when the
server sends one.

## State and locking

State lives in `<home>/.state/fetches.json`, written atomically: serialise to a
temp file, then `replace()`. A crash mid-write leaves the previous state intact
rather than a truncated file.

Locking is a per-model lock file created with `O_CREAT | O_EXCL`, recording the
PID and a timestamp. On collision, vanity reads the PID and checks liveness:

- **dead** — the lock is stale (a `SIGKILL`ed fetch), so break it and continue
- **alive** — refuse; two fetches into one directory would corrupt it
- **unreadable** — refuse, rather than guess

Liveness is portable. POSIX uses the signal-0 probe; Windows has no such thing
(`os.kill` there terminates rather than probes), so it queries the process
handle instead.

Concurrent state writes are serialised behind a threading lock, since `--jobs N`
has several fetches updating one file.

## Backends

All three implement the same contract: given a `Model`, put its content at
`model.path`, or raise. They are selected per fetch and produce interchangeable
results — the bytes on disk are just files.

The git backends share a command runner that streams output, emits a heartbeat
with a cached size sample, and captures stderr to a temp file so a failure can
be classified. The size sample is cached and refreshed every Nth beat: walking a
300GB tree on every heartbeat costs more than the heartbeat is worth.

The http backend is the one to read if you are auditing: `urllib`, a tree
listing, per-file downloads with `Range`-based resume, and a size check before
`.part` is moved into place.

## Errors

```
FetchError            everything below is one
├── HttpFailure       carries url, status, Retry-After
└── GitFailure        carries returncode, cmd, stderr
```

Backends raise the specific type; the retry loop uses the payload to decide
whether to back off. `cli.main()` catches `FetchError` and turns it into a
message and exit code, so a failure is a diagnostic rather than a traceback.

## Design decisions

**Stdlib only, enforced by a test.** Not a policy anyone has to remember:
`test_stdlib_only.py` walks the package by AST and fails on any import outside
`sys.stdlib_module_names`. The claim is the product, so it is checked
mechanically.

**The registry is data, not code.** It was Python literals once. Data is
diffable, reviewable, editable by tools, and safe for an agent to modify —
none of which is true of a Python module.

**Free-text `runtime` and `role`.** They are notes to your future self about why
a model is in the library. Constraining them to an enum would mean picking the
enum for everyone, and being wrong.

**Validation fails loudly.** A manifest that tolerates a dangling reference will
eventually fetch the wrong thing. Better to refuse at load.

**Atomic everything.** State writes, `.part` files, lock creation. The assumed
failure mode is a machine losing power during a 300GB download, because that is
the actual failure mode.

## Tests

The suite is fast (a few seconds) and hermetic — no network, no writes outside
temp directories. Some of it pins behaviour that is easy to regress silently:

- `test_stdlib_only.py` — the dependency claim
- `test_readme_example.py` — the README's registry example, run through the real loader
- `test_pinned_revision.py` — that a SHA is never passed to `git clone --branch`
- `test_config_resolution.py` — resolution order, and that the first-run prompt
  never fires without a TTY

See [CONTRIBUTING.md](../CONTRIBUTING.md).
