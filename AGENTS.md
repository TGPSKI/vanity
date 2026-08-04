# Vanity Agent Guide

Routing: [docs/GUIDE.md](docs/GUIDE.md) for usage and the command reference,
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for internals,
[CONTRIBUTING.md](CONTRIBUTING.md) for the invariants a change must preserve.

The source of truth for fetchable models, sets, aliases, and lifecycle behavior is the set of JSON files under `registry/`, validated against `schemas/modelset.schema.json`.

`registry/` holds the operator's own library and is not tracked by git. `examples/registry/starter.json` is the shipped example; treat it as a template, not as state to edit.

## Selection Rules

The selection rules below are example policy. Replace them as your library evolves — the loader treats every model entry equally as long as it is in a `registry/*.json` file and the `verify` gate passes.

For GPU LLM serving, prefer repositories that match the intended runtime:

1. Prefer vLLM-compatible `safetensors` checkpoints.
2. Prefer vendor/publisher optimized Blackwell-friendly quantizations when available, especially NVFP4/MXFP4/FP8/AWQ.
3. Do not choose GGUF for GPU LLM slots unless the target runtime is explicitly llama.cpp.
4. Use GGUF only for a separate llama.cpp profile, not for the default vLLM library.
5. For non-LLM sidecars, match the domain runtime instead of vLLM:
   - embeddings: sentence-transformers/TEI-compatible repos are acceptable
   - rerankers: cross-encoder or model-specific reranker repos are acceptable
   - STT: CTranslate2/faster-whisper repos are acceptable
   - TTS: native TTS model repos are acceptable

If the exact reference model does not exist, choose the closest model that satisfies the role and runtime:

- Same model family and role beats same quant label.
- vLLM-compatible quant beats exact-name GGUF.
- Apache 2.0 / MIT options are preferred when capability is comparable.
- Avoid huge BF16 checkpoints when a high-quality quantized checkpoint exists and the role is runtime evaluation.

## Verification Rules

Prefer `vanity add <org>/<repo>`, which resolves the repo, measures it, pins its
current commit, and refuses anything that does not exist -- the rule below,
enforced in code. Use `--yes` to accept the derived values without prompting.

To check a candidate by hand before adding it:

```sh
git ls-remote https://huggingface.co/<org>/<repo> HEAD
```

or, when the repo may be gated:

```sh
curl -LsS -o /tmp/hf_check.json -w '%{http_code}\n' https://huggingface.co/api/models/<org>/<repo>
```

Do not add repos that only appear in prose unless the repository resolves.

## Grouping Rules

Each `registry/*.json` file defines one coherent set of models. The file stem (without `.json`) acts as a fetchable set name. When adding a model:

1. Run `vanity add <org>/<repo> --file <set>`, or edit `registry/<set>.json` directly (add to `"models"`, and to `"sets"` where needed).
2. Optionally add an `"aliases"` entry when renaming keys from previous versions.
3. Run `make list && make verify` to confirm the loader resolves everything cleanly.

## Fetching

Fetch via the Makefile, which is the universal entry point:

```sh
make fetch TARGET=<model-key|set-name|registry-file-stem|all>
make fetch-bg TARGET=<model-key|set-name|registry-file-stem|all>
```

For background fetching, keep the mechanism simple — use your shell:

```sh
mkdir -p logs
make fetch-bg TARGET=my-set
tail -f logs/fetch-my-set.log
```

Report the PID plus log path. Do not introduce service managers unless the operator explicitly asks for one.