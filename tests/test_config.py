"""Corpus dirs must not be created at import if the volume is missing."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from citehop.config import _DEFAULT_CORPORA, _resolve_dir, storage_warning, try_mkdir


class ConfigStorageTests(unittest.TestCase):
    def test_try_mkdir_does_not_raise_on_unwritable_parent(self) -> None:
        self.assertFalse(try_mkdir(Path("/root/CiteHopWouldFail")))

    def test_resolve_dir_returns_path_when_mkdir_fails(self) -> None:
        path = _resolve_dir("/root/CiteHopWouldFail", _DEFAULT_CORPORA)
        self.assertEqual(path, Path("/root/CiteHopWouldFail"))

    def test_import_does_not_require_vault(self) -> None:
        msg = storage_warning()
        if os.path.isdir(_DEFAULT_CORPORA):
            self.assertEqual(msg, "")
        else:
            self.assertIn("not mounted", msg)

    def test_try_mkdir_ok_in_tmp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "CiteHop"
            self.assertTrue(try_mkdir(dest))
            self.assertTrue(dest.is_dir())


if __name__ == "__main__":
    unittest.main()
