"""Guard the headline claim: vanity imports nothing but the standard library.

The README states that everything under src/vanity/ imports only stdlib.
That claim is the product's whole pitch, so it is enforced here rather than
left to review discipline -- adding a third-party import fails the suite.

Run: python3 -m unittest discover -s tests -p 'test_stdlib_only*'
"""

import ast
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "vanity"

# Package-relative imports resolve to this name, not a distribution.
LOCAL_PACKAGE = "vanity"


def _toplevel_imports(path: Path) -> set[str]:
    """Return top-level module names imported by a source file."""
    mods: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name.split(".")[0])
        # level > 0 is a relative import (from .cli import main) -- local.
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module.split(".")[0])
    return mods


class TestStdlibOnly(unittest.TestCase):
    def test_sources_present(self) -> None:
        """Guard against the walk silently finding nothing."""
        self.assertTrue(SRC.is_dir(), f"package dir missing: {SRC}")
        self.assertGreater(len(list(SRC.rglob("*.py"))), 0, "no sources found")

    def test_no_third_party_imports(self) -> None:
        offenders: dict[str, set[str]] = {}

        for path in sorted(SRC.rglob("*.py")):
            for mod in _toplevel_imports(path):
                if mod == LOCAL_PACKAGE or mod in sys.stdlib_module_names:
                    continue
                offenders.setdefault(str(path.relative_to(SRC.parent.parent)), set()).add(mod)

        self.assertEqual(
            offenders,
            {},
            "vanity must import only the standard library; third-party imports found: "
            f"{ {k: sorted(v) for k, v in offenders.items()} }",
        )


if __name__ == "__main__":
    unittest.main()
