"""Named seeds: save/load, slug, CLI --preset."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from citehop.seed import (
    SeedQuery,
    get_named_seed,
    load_named_seeds,
    normalize_seed_name,
    query_from_args,
    save_named_seed,
)


class NamedSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patcher = patch("citehop.seed.CONFIG_DIR", self.root)
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.tmp.cleanup()

    def test_qc4hep_is_built_in_without_a_file(self) -> None:
        seeds = load_named_seeds()
        self.assertIn("qc4hep", seeds)
        self.assertFalse((self.root / "named_seeds.json").exists())
        q = seeds["qc4hep"]
        self.assertEqual(q.slug(), "qc4hep")
        self.assertEqual(q.fingerprint(), "preset:qc4hep")
        self.assertIn("Di Meglio", q.author or "")

    def test_save_and_load_user_seed(self) -> None:
        q = SeedQuery(arxiv_id="1706.03762", title="Attention Is All You Need", author="Vaswani")
        key = save_named_seed("Attention", q)
        self.assertEqual(key, "attention")
        self.assertTrue((self.root / "named_seeds.json").is_file())
        loaded = get_named_seed("attention")
        assert loaded is not None
        self.assertEqual(loaded.arxiv_id, "1706.03762")
        self.assertEqual(loaded.slug(), "attention")
        self.assertIn("attention", load_named_seeds())
        self.assertIn("qc4hep", load_named_seeds())

    def test_user_file_overrides_builtin_qc4hep(self) -> None:
        save_named_seed(
            "qc4hep",
            SeedQuery(title="Override", author="X", pdf=Path("/tmp/other.pdf")),
        )
        q = get_named_seed("qc4hep")
        assert q is not None
        self.assertEqual(q.title, "Override")
        self.assertEqual(q.pdf, Path("/tmp/other.pdf").resolve())

    def test_normalize_rejects_doi_like_and_empty(self) -> None:
        with self.assertRaises(ValueError):
            normalize_seed_name("10.1234/foo")
        with self.assertRaises(ValueError):
            normalize_seed_name("")
        with self.assertRaises(ValueError):
            normalize_seed_name("_projects")
        self.assertEqual(normalize_seed_name("My Seed"), "my-seed")

    def test_query_from_args_preset(self) -> None:
        save_named_seed("vaswani", SeedQuery(arxiv_id="1706.03762", title="Attention"))
        q = query_from_args(SimpleNamespace(preset="vaswani", doi=None, arxiv=None, title=None, author=None, venue=None, year=None, pdf=None))
        self.assertEqual(q.preset, "vaswani")
        self.assertEqual(q.arxiv_id, "1706.03762")
        self.assertEqual(q.slug(), "vaswani")

    def test_query_from_args_unknown_preset(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            query_from_args(SimpleNamespace(preset="no-such-seed"))
        self.assertIn("Unknown named seed", str(ctx.exception))

    def test_save_requires_identifier(self) -> None:
        with self.assertRaises(ValueError):
            save_named_seed("empty", SeedQuery())


if __name__ == "__main__":
    unittest.main()
