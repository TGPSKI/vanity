"""Tests for `vanity add` -- reference parsing, field inference, safe writes.

Network calls are stubbed: these pin the derivation rules and the write path,
not Hugging Face's availability.

Run: python3 -m unittest discover -s tests -p 'test_onboard*'
"""

import json
import tempfile
import unittest
from pathlib import Path

from tests.util import load_vanity

vanity = load_vanity()
from vanity import onboard  # noqa: E402
from vanity.util import FetchError  # noqa: E402


class TestParseRepoRef(unittest.TestCase):
    def test_accepted_forms_all_normalise(self) -> None:
        for text in (
            "Qwen/QwQ-32B-AWQ",
            "  Qwen/QwQ-32B-AWQ  ",
            "https://huggingface.co/Qwen/QwQ-32B-AWQ",
            "http://huggingface.co/Qwen/QwQ-32B-AWQ/",
            "huggingface.co/Qwen/QwQ-32B-AWQ",
            "hf.co/Qwen/QwQ-32B-AWQ",
            "https://huggingface.co/Qwen/QwQ-32B-AWQ/tree/main",
            "https://huggingface.co/Qwen/QwQ-32B-AWQ?library=true",
        ):
            with self.subTest(text=text):
                self.assertEqual(onboard.parse_repo_ref(text), "Qwen/QwQ-32B-AWQ")

    def test_rejects_nonsense(self) -> None:
        for text in ("", "   ", "not a repo at all", "onlyname", "a/b/c/d"):
            with self.subTest(text=text), self.assertRaises(FetchError):
                onboard.parse_repo_ref(text)


class TestSuggestKey(unittest.TestCase):
    def test_derives_a_valid_key(self) -> None:
        from vanity.registry import MODEL_KEY_RE

        for repo, expected in (
            ("Qwen/QwQ-32B-AWQ", "qwq-32b-awq"),
            ("BAAI/bge-m3", "bge-m3"),
            ("nomic-ai/nomic-embed-text-v1.5", "nomic-embed-text-v15"),
            ("hexgrad/Kokoro-82M", "kokoro-82m"),
        ):
            with self.subTest(repo=repo):
                key = onboard.suggest_key(repo)
                self.assertEqual(key, expected)
                self.assertTrue(MODEL_KEY_RE.match(key), f"{key} is not a legal key")


class TestSuggestRuntime(unittest.TestCase):
    def _runtime(self, repo, tags=(), pipeline="", library="", files=()):
        info = {"tags": list(tags), "pipeline_tag": pipeline, "library_name": library}
        return onboard.suggest_runtime(repo, info, list(files))

    def test_quantised_text_generation(self) -> None:
        self.assertEqual(
            self._runtime("Qwen/QwQ-32B-AWQ", tags=["safetensors", "awq", "4-bit"],
                          pipeline="text-generation", files=["model.safetensors"]),
            "vLLM / AWQ",
        )

    def test_more_specific_quant_wins(self) -> None:
        self.assertEqual(
            self._runtime("nvidia/Qwen3.6-27B-NVFP4", tags=["safetensors", "4-bit", "nvfp4"],
                          pipeline="text-generation", files=["model.safetensors"]),
            "vLLM / NVFP4",
        )

    def test_unquantised_defaults_to_bf16(self) -> None:
        self.assertEqual(
            self._runtime("Qwen/Qwen3-Coder-Next", tags=["safetensors"],
                          pipeline="text-generation", files=["model.safetensors"]),
            "vLLM / BF16",
        )

    def test_gguf_routes_to_llama_cpp(self) -> None:
        self.assertEqual(
            self._runtime("someone/model-GGUF", files=["model-q4.gguf"]),
            "llama.cpp / GGUF",
        )

    def test_embeddings(self) -> None:
        self.assertEqual(
            self._runtime("BAAI/bge-m3", library="sentence-transformers",
                          pipeline="sentence-similarity"),
            "sentence-transformers / TEI",
        )

    def test_ctranslate2_speech_from_library_field(self) -> None:
        """What the real repo reports: library_name/tag ctranslate2."""
        self.assertEqual(
            self._runtime("deepdml/faster-whisper-large-v3-turbo-ct2",
                          tags=["ctranslate2", "audio"], library="ctranslate2",
                          pipeline="automatic-speech-recognition"),
            "CTranslate2 / faster-whisper",
        )

    def test_ctranslate2_inferred_from_repo_name(self) -> None:
        """Fallback for conversions that set neither tag nor library."""
        self.assertEqual(
            self._runtime("someone/whisper-small-ct2",
                          pipeline="automatic-speech-recognition"),
            "CTranslate2 / faster-whisper",
        )

    def test_plain_asr_is_not_ctranslate2(self) -> None:
        self.assertEqual(
            self._runtime("openai/whisper-large-v3",
                          pipeline="automatic-speech-recognition"),
            "transformers / ASR",
        )

    def test_encoder_classifier(self) -> None:
        self.assertEqual(
            self._runtime("microsoft/deberta-v3-large", pipeline="fill-mask"),
            "transformers / encoder",
        )


class TestSuggestRole(unittest.TestCase):
    def test_known_pipelines(self) -> None:
        self.assertEqual(onboard.suggest_role({"pipeline_tag": "text-generation"}), "text generation")
        self.assertEqual(onboard.suggest_role({"pipeline_tag": "sentence-similarity"}), "embedding")
        self.assertEqual(onboard.suggest_role({"pipeline_tag": "text-to-speech"}), "text-to-speech")

    def test_unknown_pipeline_is_humanised(self) -> None:
        self.assertEqual(onboard.suggest_role({"pipeline_tag": "depth-estimation"}), "depth estimation")

    def test_missing_pipeline(self) -> None:
        self.assertEqual(onboard.suggest_role({}), "unspecified")


class TestSizeHint(unittest.TestCase):
    def test_formats_with_tilde(self) -> None:
        self.assertEqual(onboard.suggest_size_hint(22 * 1024 ** 3), "~22.0GB")

    def test_unknown_size_is_blank(self) -> None:
        self.assertEqual(onboard.suggest_size_hint(None), "")
        self.assertEqual(onboard.suggest_size_hint(0), "")


class TestAddToRegistryFile(unittest.TestCase):
    def test_insert_preserves_other_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "serving.json"
            path.write_text(json.dumps({
                "description": "Primary runtime models",
                "models": {"existing": {"repo": "o/E", "runtime": "r", "role": "x"}},
                "sets": {"s": ["existing"]},
            }))

            onboard.add_to_registry_file(path, "added", {"repo": "o/A", "runtime": "r", "role": "y"})
            doc = json.loads(path.read_text())

        self.assertEqual(doc["description"], "Primary runtime models")
        self.assertEqual(doc["sets"], {"s": ["existing"]})
        self.assertEqual(sorted(doc["models"]), ["added", "existing"])

    def test_refuses_duplicate_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "serving.json"
            path.write_text(json.dumps({"models": {"dup": {"repo": "o/D", "runtime": "r", "role": "x"}}}))
            with self.assertRaises(FetchError):
                onboard.add_to_registry_file(path, "dup", {"repo": "o/X", "runtime": "r", "role": "y"})

    def test_creates_a_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fresh.json"
            onboard.add_to_registry_file(path, "first", {"repo": "o/F", "runtime": "r", "role": "z"})
            self.assertEqual(list(json.loads(path.read_text())["models"]), ["first"])

    def test_result_loads_as_a_registry(self) -> None:
        """What add writes must be something load_registry accepts."""
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            onboard.add_to_registry_file(
                directory / "serving.json",
                "qwq-32b-awq",
                {"repo": "Qwen/QwQ-32B-AWQ", "runtime": "vLLM / AWQ",
                 "role": "reasoning", "size_hint": "~22GB",
                 "revision": "dc9f21221581580ccfa51b74077db6056b56cb69"},
            )
            reg = load_vanity().registry.load_registry(directory)

        self.assertIn("qwq-32b-awq", reg.models)
        self.assertEqual(reg.models["qwq-32b-awq"].revision,
                         "dc9f21221581580ccfa51b74077db6056b56cb69")


class TestDescribe(unittest.TestCase):
    """describe() folds the two API calls into the fields add needs."""

    def test_fields_derived_from_metadata(self) -> None:
        info = {
            "sha": "dc9f21221581580ccfa51b74077db6056b56cb69",
            "pipeline_tag": "text-generation",
            "tags": ["safetensors", "awq", "4-bit"],
            "gated": False,
        }
        files = ["model-00001-of-00005.safetensors", "config.json"]

        original_info, original_tree = onboard.model_info, onboard.repo_files_and_size
        onboard.model_info = lambda repo: info
        onboard.repo_files_and_size = lambda repo, rev: (files, 22 * 1024 ** 3)
        try:
            found = onboard.describe("Qwen/QwQ-32B-AWQ")
        finally:
            onboard.model_info, onboard.repo_files_and_size = original_info, original_tree

        self.assertEqual(found["key"], "qwq-32b-awq")
        self.assertEqual(found["revision"], info["sha"])
        self.assertEqual(found["runtime"], "vLLM / AWQ")
        self.assertEqual(found["role"], "text generation")
        self.assertEqual(found["size_hint"], "~22.0GB")
        self.assertFalse(found["gated"])
        self.assertEqual(found["file_count"], 2)

    def test_gated_flag_survives(self) -> None:
        original_info, original_tree = onboard.model_info, onboard.repo_files_and_size
        onboard.model_info = lambda repo: {"sha": "abc123", "gated": "manual", "tags": []}
        onboard.repo_files_and_size = lambda repo, rev: ([], None)
        try:
            found = onboard.describe("google/gemma-3-27b-it")
        finally:
            onboard.model_info, onboard.repo_files_and_size = original_info, original_tree

        self.assertTrue(found["gated"])
        self.assertEqual(found["size_hint"], "")


if __name__ == "__main__":
    unittest.main()


class TestBootstrap(unittest.TestCase):
    """`add` must work before a registry exists -- it is the first command a
    new user runs, and it used to fail with "registry directory not found"."""

    def _args(self, **over):
        import argparse
        base = dict(repo=None, file=None, key=None, runtime=None, role=None,
                    size_hint=None, revision=None, no_pin=False, create=False,
                    yes=True, registry_dir=None)
        base.update(over)
        return argparse.Namespace(**base)

    def test_missing_registry_yields_empty_and_flags_bootstrap(self) -> None:
        from vanity import cli

        with tempfile.TemporaryDirectory() as tmp:
            registry, bootstrapping = cli._load_or_bootstrap(Path(tmp) / "registry")

        self.assertTrue(bootstrapping)
        self.assertEqual(registry.models, {})
        self.assertEqual(registry.files, {})

    def test_empty_registry_dir_also_bootstraps(self) -> None:
        from vanity import cli

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "registry"
            directory.mkdir()
            _, bootstrapping = cli._load_or_bootstrap(directory)

        self.assertTrue(bootstrapping)

    def test_populated_registry_is_not_bootstrap(self) -> None:
        from vanity import cli

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "serving.json").write_text(json.dumps(
                {"models": {"m": {"repo": "o/M", "runtime": "r", "role": "x"}}}))
            registry, bootstrapping = cli._load_or_bootstrap(directory)

        self.assertFalse(bootstrapping)
        self.assertIn("m", registry.models)

    def test_a_broken_registry_still_raises(self) -> None:
        """A real error must not be mistaken for a fresh start."""
        from vanity import cli

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "bad.json").write_text(json.dumps(
                {"models": {"m": {"repo": "o/M", "runtime": "r", "role": "x"}},
                 "sets": {"s": ["missing-model"]}}))
            with self.assertRaises(SystemExit):
                cli._load_or_bootstrap(directory)


class TestEmptyStateGuidance(unittest.TestCase):
    def test_missing_registry_message_points_at_add(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit) as ctx:
            load_vanity().registry.load_registry(Path(tmp) / "nope")
        self.assertIn("vanity add", str(ctx.exception))

    def test_empty_registry_message_points_at_add(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit) as ctx:
            load_vanity().registry.load_registry(Path(tmp))
        self.assertIn("vanity add", str(ctx.exception))
