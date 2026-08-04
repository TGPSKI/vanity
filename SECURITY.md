# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.2.x   | :white_check_mark: |
| 0.1.x   | :x:                |

## Reporting a Vulnerability

If you discover a potential security vulnerability, please submit a GitHub issue.

## Trust model

vanity is a **single-operator tool**. It runs as you, on your machine, against
repositories you name. It exposes no network service, opens no ports, and has no
daemon — every run is a one-shot process you invoked.

What it does with your authority:

- **Reads and writes your library directory.** Fetching writes model content
  under the configured store; `remove` deletes a model's directory.
- **Reads and writes your registry and state.** Both are plain files in
  directories you configured.
- **Makes outbound HTTPS requests to Hugging Face**, and shells out to `git`
  when a git backend is selected.

There is no sandboxing between vanity and the rest of your account, and none is
claimed.

## Known limits

- **Model content is not verified against a checksum.** A pinned `revision`
  names a git commit; it does not prove the bytes you received hash to anything
  expected. Pinning gives you reproducibility and an audit trail, not integrity
  verification. See [docs/GUIDE.md](docs/GUIDE.md#provenance) for what a pin
  does and does not guarantee.
- **A pin does not survive upstream history rewrites.** If a repo is force-pushed
  or deleted, the pinned commit becomes unfetchable. The registry still records
  what you had, but it is not an archive.
- **Model weights are executable content in practice.** vanity downloads files;
  it does not load, deserialise, or execute them. Whatever you point at those
  weights does. Prefer `safetensors` over pickle-based formats, and treat an
  untrusted repo the way you would treat untrusted code.
- **`git` subprocesses inherit your environment.** The git backends invoke
  `git`, which respects your global git configuration, credential helpers, and
  any configured hooks. The `http` backend shells out to nothing.
- **Registry files are trusted input.** A registry is configuration you wrote.
  vanity validates its structure and refuses malformed entries, but a registry
  that names a hostile repository will fetch from it. Review registry diffs the
  way you review code.
- **State and library files are not encrypted at rest.** They carry ordinary
  filesystem permissions. Encrypt the underlying volume if that matters.

## Credentials

`HF_TOKEN` (or `HUGGING_FACE_HUB_TOKEN`) is read from the environment or from a
`.env` file at the repo root. `.env` is gitignored.

- The token is sent as a bearer header to Hugging Face over HTTPS, and passed to
  `git` through a `GIT_ASKPASS` helper written to the state directory with mode
  `0700`.
- It is never written to the registry, the state file, or a model sidecar.
- A token is **not required** for public models. It raises rate limits and is
  required only for gated or private repositories — scope it accordingly, and
  prefer a read-only token.

If a git operation fails authentication with a token set, vanity retries once
without it, which is what public repositories need. If that surprises you in
your environment, unset the token explicitly.

## Supply chain

- **No runtime dependencies.** `pyproject.toml` declares `dependencies = []`,
  and `tests/test_stdlib_only.py` fails the build if any import resolves outside
  the standard library. There is no third-party package to compromise, and no
  lockfile to audit.
- **CI actions are pinned to commit SHAs**, not tags, so a moved tag cannot
  change what runs in the build.
- **Release tags are protected** against modification and deletion by a
  repository ruleset, and commits require signatures.
