"""URL construction for the http backend.

repo_tree used quote(repo, safe="") which percent-encodes the slash in
org/repo. The HF API rejects that with 400 "Invalid repo name: ... repo name
includes an url-encoded slash", so the pure-stdlib http path -- the backend the
zero-dependency claim rests on -- failed for every repo.

Run: python3 -m unittest discover -s tests -p 'test_http_urls*'
"""

import unittest

from tests.util import load_vanity

vanity = load_vanity()
from vanity import httpfetch  # noqa: E402
from vanity.registry import Model  # noqa: E402


def _model(repo="org/Repo", revision="main") -> Model:
    return Model(key="m", repo=repo, size_hint="0B", runtime="t", role="t",
                 revision=revision)


class TestRepoTreeUrl(unittest.TestCase):
    def _url(self, model: Model) -> str:
        captured = {}

        def fake_request_bytes(url, headers=None, timeout=45.0):
            captured["url"] = url
            return b"[]"

        original = httpfetch.request_bytes
        httpfetch.request_bytes = fake_request_bytes
        try:
            httpfetch.repo_tree(model)
        finally:
            httpfetch.request_bytes = original
        return captured["url"]

    def test_slash_is_not_encoded(self) -> None:
        url = self._url(_model())
        self.assertIn("/api/models/org/Repo/tree/main", url)
        self.assertNotIn("%2F", url)

    def test_pinned_revision_appears_in_path(self) -> None:
        sha = "71034c5d8bde858ff824298bdedc65515b97d2b9"
        self.assertIn(f"/tree/{sha}", self._url(_model(revision=sha)))


class TestResolveUrl(unittest.TestCase):
    def test_repo_slash_preserved(self) -> None:
        url = httpfetch.resolve_url(_model(), "config.json")
        self.assertEqual(
            url, "https://huggingface.co/org/Repo/resolve/main/config.json"
        )

    def test_nested_file_path(self) -> None:
        url = httpfetch.resolve_url(_model(), "subdir/model-00001.safetensors")
        self.assertIn("/resolve/main/subdir/model-00001.safetensors", url)

    def test_space_in_filename_is_encoded(self) -> None:
        self.assertIn("%20", httpfetch.resolve_url(_model(), "a file.json"))


class TestHeaders(unittest.TestCase):
    def test_user_agent_carries_real_version(self) -> None:
        from vanity import __version__

        self.assertEqual(
            httpfetch.hf_headers()["User-Agent"], f"vanity/{__version__}"
        )

    def test_extra_headers_merge(self) -> None:
        headers = httpfetch.hf_headers({"Range": "bytes=10-"})
        self.assertEqual(headers["Range"], "bytes=10-")
        self.assertIn("User-Agent", headers)


if __name__ == "__main__":
    unittest.main()
