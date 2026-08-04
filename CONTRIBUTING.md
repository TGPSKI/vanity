# Contributing to vanity

Thanks for your interest. vanity holds a few hard constraints to stay a
lightweight, auditable manifest manager — they shape what a good change looks
like here.

## Architectural constraints

- **Standard library only.** `pyproject.toml` must keep `dependencies = []`, and
  nothing under `src/vanity/` may import outside `sys.stdlib_module_names`.
  Adding a dependency is a decision about what the project *is*, not a
  convenience. `tests/test_stdlib_only.py` enforces this.
- **Fail closed.** A registry vanity doesn't fully understand is refused, by
  name, at load. A manifest that tolerates a dangling reference eventually
  fetches the wrong thing.
- **Runs from a clone.** `make <target>` must work with no install and no
  virtualenv. `pip install -e .` is a convenience, never a requirement.
- **Atomic on disk.** State writes, `.part` files, and lock creation all assume
  the machine can lose power during a 300GB download, because that is the actual
  failure mode.

## Development setup

Requires Python 3.10+.

```bash
make test          # unittest suite, straight from src/
make dev-check     # compile + test — run this before every commit
make lint          # ruff, if installed
```

No network access is needed; the suite is hermetic and runs in a few seconds.

`pytest` works too if you prefer its output:

```bash
PYTHONPATH=src python -m pytest tests/ -q
```

## Invariants that regress quietly

Each of these has a test because each has been broken before.

| Invariant | Test |
|---|---|
| Only the standard library is imported | `test_stdlib_only.py` |
| Tests never touch the real library or state | redirect `MODEL_STORE` **and** `config.state_dir` |
| No prompt without a TTY | `test_config_resolution.py` |
| Documented behaviour matches the code | `test_readme_example.py` |
| A commit SHA is never passed to `git clone --branch` | `test_pinned_revision.py` |

The second is worth spelling out: a test once built a temp store and then never
used it, so running the suite wrote a fake model into the real library and the
real `fetches.json`. Redirect both, always.

The fourth exists because the documented registry format drifted from the parser
— the format in the README could not actually be loaded.
`test_readme_example.py` extracts the README's example and runs it through the
real loader.

## Adding a test

```python
from tests.util import load_vanity

vanity = load_vanity()
from vanity import registry  # noqa: E402
```

Use `tempfile.TemporaryDirectory()` for anything touching the filesystem, and
redirect config explicitly rather than relying on the ambient environment.

Where a test pins subtle behaviour, say why in the docstring. Several existing
tests exist because of a specific bug, and the docstring is what tells the next
person whether a failure is a regression or an intentional change.

## Style

Match what's there: module-level imports (function-local only to break a cycle),
`Path` over string paths, f-strings, type hints on new functions.

Comments explain **why**, not what. The useful ones here record a constraint that
isn't obvious from reading — that `git clone --branch` rejects SHAs, that the HF
API rejects an encoded slash in `org/repo`, that `pull --ff-only` can't run on a
detached HEAD. Those save the next person an afternoon.

## Pull requests

1. `make dev-check` before you start, so you know the baseline is green.
2. Make the change, with a test that fails without it.
3. `make dev-check` again.
4. Update `docs/` and the README in the same commit if behaviour changed.
5. Add a `CHANGELOG.md` entry under `## [Unreleased]`.

CI runs a fast gate on every PR. Cross-version and slower checks run when a
maintainer adds the `full-test` label, on `workflow_dispatch`, or on push to
`main` — add the label if your change touches the fetch path, configuration
resolution, or anything platform-specific.

Commit messages here explain the *why* — what was wrong and what the fix
assumes. `git log` is the best documentation of this project's sharp edges.

## Reporting a bug

Include what you ran, what happened, and:

```bash
vanity config
vanity doctor
python -VV
```

Most confusion turns out to be a store or registry resolving somewhere other
than expected, and `vanity config` shows that immediately.
