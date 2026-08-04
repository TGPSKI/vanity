"""Tests for SizeSampler — cheapens the heartbeat size walk."""

import unittest
from pathlib import Path

from tests.util import load_vanity

SizeSampler = load_vanity().gitfetch.SizeSampler


class InvokeCountingWalker:
    """A test walker that counts invocations and returns an increasing value."""

    def __init__(self) -> None:
        self.call_count: int = 0

    def __call__(self, path: Path) -> int:
        self.call_count += 1
        return self.call_count * 1000


class TestSizeSampler(unittest.TestCase):
    """Core caching and invocation-counting tests."""

    def test_caches_recomputes_every(self) -> None:
        walker = InvokeCountingWalker()
        sampler = SizeSampler(Path("/fake"), walker=walker, every=5)

        results = [sampler.sample() for _ in range(10)]

        # Exactly 2 walker calls: on call 1 and call 6
        self.assertEqual(walker.call_count, 2)

        # Call 1 returns the fresh value 1000
        self.assertEqual(results[0], (1000, True))

        # Calls 2–5 return same value, not fresh
        for i in range(1, 5):
            self.assertEqual(results[i], (1000, False))

        # Call 6 recomputes → 2000, fresh=True
        self.assertEqual(results[5], (2000, True))

        # Calls 7–10 return cached 2000
        for i in range(6, 10):
            self.assertEqual(results[i], (2000, False))

    def test_every_one_always_fresh(self) -> None:
        walker = InvokeCountingWalker()
        sampler = SizeSampler(Path("/fake"), walker=walker, every=1)

        results = [sampler.sample() for _ in range(5)]

        self.assertEqual(walker.call_count, 5)
        for i in range(5):
            self.assertEqual(results[i], ((i + 1) * 1000, True))

    def test_default_every_is_five(self) -> None:
        walker = InvokeCountingWalker()
        sampler = SizeSampler(Path("/fake"), walker=walker)

        # Call 1: first call is always fresh
        s, f = sampler.sample()
        self.assertTrue(f)
        self.assertEqual(walker.call_count, 1)

        # Calls 2–5: all cached
        results = [sampler.sample() for _ in range(4)]
        for s_val, f_val in results:
            self.assertFalse(f_val)
            self.assertEqual(s_val, 1000)
        self.assertEqual(walker.call_count, 1)

        # Call 6: triggers recomputation
        s6, f6 = sampler.sample()
        self.assertTrue(f6)
        self.assertEqual(s6, 2000)
        self.assertEqual(walker.call_count, 2)

    def test_returns_zero_for_nonexistent_path(self) -> None:
        walker = InvokeCountingWalker()
        sampler = SizeSampler(Path("/nonexistent/does/not/exist"), walker=walker)

        size, fresh = sampler.sample()
        self.assertEqual(size, 1000)  # walker still returns 1000 (call_count=1 * 1000)
        self.assertTrue(fresh)


if __name__ == "__main__":
    unittest.main()