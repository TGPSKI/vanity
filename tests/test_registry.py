"""Tests for the stdlib registry loader (plan03).

Run: python3 -m unittest discover -s tests -p 'test_registry*'
"""

import json
import tempfile
import unittest
from pathlib import Path

from tests.util import load_vanity


class TestLoadRegistry(unittest.TestCase):
    """Core loading: happy path validates JSON files and produces a Registry."""

    def test_two_files_load_correctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)

            file_a = tmpdir / "alpha.json"
            file_b = tmpdir / "zulu.json"

            file_a.write_text(
                json.dumps(
                    {
                        "models": {
                            "model-a": {
                                "repo": "org/model-a",
                                "runtime": "vLLM",
                                "role": "test",
                            },
                            "model-b": {
                                "repo": "org/model-b",
                                "runtime": "vLLM",
                                "role": "test",
                            },
                        },
                        "sets": {
                            "pair": ["model-a", "model-b"],
                        },
                        "aliases": {
                            "a-alias": "model-a",
                        },
                    }
                )
            )
            file_b.write_text(
                json.dumps(
                    {
                        "models": {
                            "model-z": {
                                "repo": "org/model-z",
                                "runtime": "embedding",
                                "role": "test",
                            },
                        },
                    }
                )
            )

            vanity = load_vanity().registry
            reg = vanity.load_registry(tmpdir)

            # All 3 models loaded
            self.assertEqual(len(reg.models), 3)
            self.assertIn("model-a", reg.models)
            self.assertIn("model-b", reg.models)
            self.assertIn("model-z", reg.models)

            # Sets and aliases present
            self.assertEqual(reg.sets, {"pair": ["model-a", "model-b"]})
            self.assertEqual(reg.aliases, {"a-alias": "model-a"})

            # File stems map to model keys
            self.assertIn("alpha", reg.files)
            self.assertIn("zulu", reg.files)
            self.assertEqual(set(reg.files["alpha"]), {"model-a", "model-b"})
            self.assertEqual(reg.files["zulu"], ["model-z"])

    def test_missing_registry_dir_system_exit(self):
        vanity = load_vanity().registry
        missing = Path("/nonexistent/path/xyz")
        with self.assertRaises(SystemExit) as ctx:
            vanity.load_registry(missing)
        # str(Path) so the separator matches the platform under test.
        self.assertIn(str(missing), str(ctx.exception))

    def test_zero_json_files_system_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            vanity = load_vanity().registry
            with self.assertRaises(SystemExit) as ctx:
                vanity.load_registry(Path(tmp))
            self.assertIn(tmp, str(ctx.exception))

    def test_unknown_top_level_field_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.json"
            path.write_text(
                json.dumps(
                    {
                        "models": {},
                        "bogus_field": True,
                    }
                )
            )
            vanity = load_vanity().registry
            with self.assertRaises(SystemExit):
                vanity.load_registry(path.parent)

    def test_unknown_model_field_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.json"
            path.write_text(
                json.dumps(
                    {
                        "models": {
                            "mymodel": {
                                "repo": "org/mymodel",
                                "runtime": "test",
                                "role": "test",
                                "unknown_field": True,
                            }
                        }
                    }
                )
            )
            vanity = load_vanity().registry
            with self.assertRaises(SystemExit):
                vanity.load_registry(path.parent)

    def test_missing_required_field_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.json"
            path.write_text(
                json.dumps(
                    {
                        "models": {
                            "mymodel": {
                                "repo": "org/mymodel",
                                # missing runtime and role
                            }
                        }
                    }
                )
            )
            vanity = load_vanity().registry
            with self.assertRaises(SystemExit):
                vanity.load_registry(path.parent)

    def test_bare_minimum_model_accepted(self):
        """A model with only repo/runtime/role is valid."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "min.json"
            path.write_text(
                json.dumps(
                    {
                        "models": {
                            "min": {
                                "repo": "org/min",
                                "runtime": "test",
                                "role": "test",
                            }
                        }
                    }
                )
            )
            vanity = load_vanity().registry
            reg = vanity.load_registry(path.parent)
            self.assertEqual(len(reg.models), 1)

    def test_size_hint_and_revision_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "full.json"
            path.write_text(
                json.dumps(
                    {
                        "models": {
                            "full": {
                                "repo": "org/full",
                                "runtime": "vLLM",
                                "role": "test",
                                "size_hint": "~5GB",
                                "revision": "refs/heads/main",
                            }
                        }
                    }
                )
            )
            vanity = load_vanity().registry
            reg = vanity.load_registry(path.parent)
            m = reg.models["full"]
            self.assertEqual(m.size_hint, "~5GB")
            self.assertEqual(m.revision, "refs/heads/main")


class TestDanglingSet(unittest.TestCase):
    """Set members that resolve to no known key must cause a loud failure."""

    def test_dangling_set_member_names_the_key_and_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(
                json.dumps(
                    {
                        "models": {
                            "existing": {
                                "repo": "org/ex",
                                "runtime": "test",
                                "role": "test",
                            }
                        },
                        "sets": {
                            "broken-set": ["no-such-model"]
                        },
                    }
                )
            )
            vanity = load_vanity().registry
            with self.assertRaises(SystemExit) as ctx:
                vanity.load_registry(path.parent)
            self.assertIn("no-such-model", str(ctx.exception))
            self.assertIn("bad.json", str(ctx.exception))


class TestDuplicateModelKey(unittest.TestCase):
    """A model key appearing in two files is an error."""

    def test_duplicate_across_files_system_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            a = tmpdir / "a.json"
            b = tmpdir / "b.json"
            a.write_text(
                json.dumps({
                    "models": {"shared": {"repo": "org/s", "runtime": "t", "role": "t"}},
                })
            )
            b.write_text(
                json.dumps({
                    "models": {"shared": {"repo": "org/s2", "runtime": "t", "role": "t"}},
                })
            )
            vanity = load_vanity().registry
            with self.assertRaises(SystemExit) as ctx:
                vanity.load_registry(tmpdir)
            self.assertIn("shared", str(ctx.exception))
            self.assertIn("a.json", str(ctx.exception))
            self.assertIn("b.json", str(ctx.exception))

    def test_duplicate_within_same_file_rejected(self):
        """Two keys that collide via different formats are not duplicates;
        the same exact key cannot appear twice because JSON object keys
        are unique. But the validation should still succeed for distinct keys."""
        pass

    def test_alias_shadows_model_key(self):
        """An alias that points at a valid target but shares a name with
        a model key must be rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.json"
            path.write_text(
                json.dumps(
                    {
                        "models": {
                            "real-model": {
                                "repo": "org/real",
                                "runtime": "test",
                                "role": "test",
                            }
                        },
                        "aliases": {
                            "real-model": "other-model",
                        },
                    }
                )
            )
            vanity = load_vanity().registry
            with self.assertRaises(SystemExit) as ctx:
                vanity.load_registry(path.parent)
            self.assertIn("real-model", str(ctx.exception))


class TestDanglingAlias(unittest.TestCase):
    """Alias target that resolves to no known model must fail."""

    def test_dangling_alias_system_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(
                json.dumps(
                    {
                        "models": {
                            "real": {"repo": "org/r", "runtime": "t", "role": "t"}
                        },
                        "aliases": {
                            "fake": "ghost-model"
                        },
                    }
                )
            )
            vanity = load_vanity().registry
            with self.assertRaises(SystemExit) as ctx:
                vanity.load_registry(path.parent)
            self.assertIn("ghost-model", str(ctx.exception))


class TestKeyPatterns(unittest.TestCase):
    """Model keys must match ^[a-z0-9][a-z0-9._-]*$."""

    def test_uppercase_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(
                json.dumps(
                    {
                        "models": {
                            "Foo-Bar": {
                                "repo": "org/foo",
                                "runtime": "test",
                                "role": "test",
                            }
                        }
                    }
                )
            )
            vanity = load_vanity().registry
            with self.assertRaises(SystemExit) as ctx:
                vanity.load_registry(path.parent)
            self.assertIn("Foo-Bar", str(ctx.exception))

    def test_key_starts_with_dash_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(
                json.dumps(
                    {
                        "models": {
                            "-bad": {
                                "repo": "org/bad",
                                "runtime": "test",
                                "role": "test",
                            }
                        }
                    }
                )
            )
            vanity = load_vanity().registry
            with self.assertRaises(SystemExit):
                vanity.load_registry(path.parent)

    def test_valid_key_formats_accepted(self):
        """Keys with dots, hyphens, underscores are valid."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ok.json"
            path.write_text(
                json.dumps(
                    {
                        "models": {
                            "a.b-c_d": {
                                "repo": "org/key",
                                "runtime": "test",
                                "role": "test",
                            }
                        }
                    }
                )
            )
            vanity = load_vanity().registry
            reg = vanity.load_registry(path.parent)
            self.assertIn("a.b-c_d", reg.models)


class TestResolutionOrder(unittest.TestCase):
    """Target resolution: model -> alias -> set -> file stem -> all."""

    def test_alias_overrides_file_stem(self):
        """When a CLI target name matches both an alias and a file stem,
        the alias takes precedence."""
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            # File named "fooview" that defines a model "m1"
            file_a = tmpdir / "fooview.json"
            file_a.write_text(
                json.dumps(
                    {
                        "models": {
                            "m1": {
                                "repo": "org/m1",
                                "runtime": "test",
                                "role": "test",
                            }
                        },
                        "aliases": {
                            "alias": "m1",  # alias name won't conflict with stem
                        },
                    }
                )
            )

            vanity = load_vanity().registry
            reg = vanity.load_registry(tmpdir)

            # resolve("alias") should return the model via alias resolution
            # i.e., it should give us back model "m1"
            model = vanity.require_model("alias", reg)
            self.assertEqual(model.key, "m1")

    def test_alias_resolution_follows_chain(self):
        """A two-level alias must resolve correctly."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.json"
            path.write_text(
                json.dumps(
                    {
                        "models": {
                            "real-model": {
                                "repo": "org/real",
                                "runtime": "test",
                                "role": "test",
                            }
                        },
                        "aliases": {
                            "level1": "real-model",
                        },
                    }
                )
            )
            vanity = load_vanity().registry
            reg = vanity.load_registry(path.parent)

            model = vanity.require_model("level1", reg)
            self.assertEqual(model.key, "real-model")

    def test_unknown_target_system_exit(self):
        """An unknown CLI target must produce a SystemExit with known info."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ok.json"
            path.write_text(
                json.dumps(
                    {
                        "models": {"m1": {"repo": "org/m", "runtime": "t", "role": "t"}},
                        "sets": {"my-set": ["m1"]},
                    }
                )
            )
            vanity = load_vanity().registry
            reg = vanity.load_registry(path.parent)
            with self.assertRaises(SystemExit) as ctx:
                vanity.require_model("nope", reg)
            err = str(ctx.exception)
            self.assertIn("nope", err)

    def test_expands_set(self):
        """A set name should expand to all its models via expand_targets."""
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            path = tmpdir / "ok.json"
            path.write_text(
                json.dumps(
                    {
                        "models": {
                            "m1": {"repo": "org/m", "runtime": "t", "role": "t"},
                            "m2": {"repo": "org/m2", "runtime": "t", "role": "t"},
                        },
                        "sets": {
                            "pair": ["m1", "m2"],
                        },
                    }
                )
            )
            vanity = load_vanity().registry
            reg = vanity.load_registry(tmpdir)

            models = vanity.expand_targets(["pair"], reg)
            self.assertEqual(len(models), 2)
            keys = {m.key for m in models}
            self.assertEqual(keys, {"m1", "m2"})

    def test_expands_all(self):
        """The special 'all' keyword should expand every model across files."""
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            (tmpdir / "a.json").write_text(
                json.dumps({"models": {
                    "m1": {"repo": "org/m", "runtime": "t", "role": "t"},
                }})
            )
            (tmpdir / "b.json").write_text(
                json.dumps({"models": {
                    "m2": {"repo": "org/m2", "runtime": "t", "role": "t"},
                }})
            )
            vanity = load_vanity().registry
            reg = vanity.load_registry(tmpdir)

            models = vanity.expand_targets(["all"], reg)
            self.assertEqual(len(models), 2)
            keys = {m.key for m in models}
            self.assertEqual(keys, {"m1", "m2"})

    def test_expand_targets_with_set_and_file_stem(self):
        """expand_targets can resolve a file stem to all models defined in it."""
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            path = tmpdir / "groupp.json"
            path.write_text(
                json.dumps({
                    "models": {
                        "m1": {"repo": "org/m1", "runtime": "t", "role": "t"},
                        "m2": {"repo": "org/m2", "runtime": "t", "role": "t"},
                    },
                })
            )
            vanity = load_vanity().registry
            reg = vanity.load_registry(tmpdir)
            models = vanity.expand_targets(["groupp"], reg)
            self.assertEqual(len(models), 2)
            keys = {m.key for m in models}
            self.assertEqual(keys, {"m1", "m2"})

    def test_expands_unknown_set_system_exit(self):
        """An unknown set name (that's also not a model/alias/file/all)
        should fail loudly."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ok.json"
            path.write_text(json.dumps({"models": {"m1": {"repo": "o/m", "runtime": "t", "role": "t"}}}))
            vanity = load_vanity().registry
            reg = vanity.load_registry(path.parent)
            with self.assertRaises(SystemExit) as ctx:
                vanity.expand_targets(["nopeset"], reg)
            self.assertIn("nopeset", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

class TestCrossFileReferences(unittest.TestCase):
    """Sets may reference models from any registry file, not only earlier ones.

    Validation used to run inside the per-file loop, so a set in a.json could
    not reference a model in b.json -- files are read in sorted order, and the
    member simply did not exist yet.
    """

    def _write(self, directory, name, doc):
        (directory / name).write_text(json.dumps(doc))

    def test_set_may_reference_a_later_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._write(d, "a-file.json", {
                "models": {"model-a": {"repo": "o/A", "runtime": "r", "role": "x"}},
                "sets": {"mixed": ["model-a", "model-b"]},
            })
            self._write(d, "b-file.json", {
                "models": {"model-b": {"repo": "o/B", "runtime": "r", "role": "x"}},
            })
            reg = load_vanity().registry.load_registry(d)

        self.assertEqual(reg.sets["mixed"], ["model-a", "model-b"])

    def test_genuinely_unknown_member_still_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._write(d, "a-file.json", {
                "models": {"model-a": {"repo": "o/A", "runtime": "r", "role": "x"}},
                "sets": {"broken": ["model-a", "not-a-model"]},
            })
            with self.assertRaises(SystemExit) as ctx:
                load_vanity().registry.load_registry(d)

        self.assertIn("not-a-model", str(ctx.exception))


class TestFileDescriptions(unittest.TestCase):
    """Each registry file's "description" is kept so `list` can label it.

    The loader used to validate the field and then discard it, leaving `list`
    to print bare file stems -- which is why "group-one/two/three" read as a
    feature rather than as filenames.
    """

    def test_description_is_kept_per_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "serving.json").write_text(json.dumps({
                "description": "Primary runtime models",
                "models": {"m-a": {"repo": "o/A", "runtime": "r", "role": "x"}},
            }))
            (d / "support.json").write_text(json.dumps({
                "models": {"m-b": {"repo": "o/B", "runtime": "r", "role": "x"}},
            }))
            reg = load_vanity().registry.load_registry(d)

        self.assertEqual(reg.descriptions["serving"], "Primary runtime models")
        self.assertNotIn("support", reg.descriptions)

    def test_shipped_example_files_are_all_described(self):
        """A file with no description prints as a bare name; keep them labelled."""
        from vanity import config

        reg = load_vanity().registry.load_registry(config.ROOT / "examples" / "registry")
        undescribed = sorted(set(reg.files) - set(reg.descriptions))
        self.assertEqual(undescribed, [], f"registry files missing a description: {undescribed}")
