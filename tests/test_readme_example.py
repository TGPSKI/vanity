"""The registry example in README.md must actually load.

The documented format had drifted from the loader (it used hf_repo/hf_file/
profiles/groups, none of which are accepted), so a new user's first copy-paste
failed validation. This pins the example to reality.

Run: python3 -m unittest discover -s tests -p 'test_readme_example*'
"""

import json
import re
import tempfile
import unittest
from pathlib import Path

from tests.util import load_vanity

vanity = load_vanity()
from vanity import registry  # noqa: E402

README = Path(__file__).resolve().parent.parent / "README.md"

# The registry example is the first fenced json block containing a "models" key.
JSON_BLOCK_RE = re.compile(r"```json\n(.*?)```", re.DOTALL)


def _registry_example() -> dict:
    blocks = JSON_BLOCK_RE.findall(README.read_text(encoding="utf-8"))
    for block in blocks:
        try:
            doc = json.loads(block)
        except json.JSONDecodeError:
            continue  # fragments (e.g. the provenance snippet) are not whole docs
        if isinstance(doc, dict) and "models" in doc:
            return doc
    raise AssertionError("no complete registry example found in README.md")


class TestReadmeRegistryExample(unittest.TestCase):
    def test_example_loads(self) -> None:
        example = _registry_example()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "example.json"
            # $schema points at a repo-relative path that will not resolve from
            # a temp dir; the loader ignores it, but keep the doc otherwise intact.
            path.write_text(json.dumps(example))

            reg = registry.load_registry(Path(tmp))

        self.assertTrue(reg.models, "example declared no models")
        for key, model in reg.models.items():
            with self.subTest(key=key):
                self.assertTrue(model.repo, f"{key} has no repo")

    def test_example_sets_resolve(self) -> None:
        """Every set member and alias target in the example must be a real key."""
        example = _registry_example()
        keys = set(example.get("models", {}))

        for name, members in example.get("sets", {}).items():
            for member in members:
                with self.subTest(set=name, member=member):
                    self.assertIn(member, keys)

        for alias, target in example.get("aliases", {}).items():
            with self.subTest(alias=alias):
                self.assertIn(target, keys)


if __name__ == "__main__":
    unittest.main()
