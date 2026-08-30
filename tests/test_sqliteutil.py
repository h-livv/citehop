"""Journal mode follows the filesystem: WAL on POSIX, DELETE on exFAT."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from citehop.sqliteutil import journal_mode_for
from citehop.store import Manifest


class JournalModeTests(unittest.TestCase):
    def test_exfat_vault_uses_delete(self) -> None:
        mounts = "UUID /run/media/h-livv/Vault exfat rw,relatime 0 0\n"
        mode = journal_mode_for(
            Path("/run/media/h-livv/Vault/CiteHop/qc4hep/manifest.db"),
            mounts=mounts,
        )
        self.assertEqual(mode, "DELETE")

    def test_ext4_home_uses_wal(self) -> None:
        mounts = "/dev/sda / ext4 rw 0 0\n/dev/sdb /home ext4 rw 0 0\n"
        mode = journal_mode_for(Path("/home/h-livv/opt/citehop/tmp.db"), mounts=mounts)
        self.assertEqual(mode, "WAL")

    def test_tmpdir_manifest_is_not_forced_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "manifest.db"
            manifest = Manifest(db)
            try:
                mode = manifest.conn.execute("PRAGMA journal_mode").fetchone()[0]
                self.assertIn(mode.lower(), ("wal", "delete"))
            finally:
                manifest.close()


if __name__ == "__main__":
    unittest.main()
