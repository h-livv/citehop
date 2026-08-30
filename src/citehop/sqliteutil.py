"""SQLite connection helpers. WAL is unsafe on exFAT (the default Vault)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Filesystems where WAL shm/locks fail and a second connection sees a stale main DB.
_UNSAFE_FS = frozenset(
    {
        "exfat",
        "vfat",
        "msdos",
        "fuseblk",
        "fuse.exfat",
        "ntfs",
        "ntfs3",
    }
)


def journal_mode_for(db_path: Path, mounts: str | None = None) -> str:
    """DELETE on exFAT/FAT/NTFS; WAL on POSIX filesystems."""
    try:
        target = str(Path(db_path).resolve())
    except OSError:
        return "DELETE"
    if mounts is None:
        try:
            mounts = Path("/proc/self/mounts").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "WAL"
    best_len = -1
    fstype = ""
    for line in mounts.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mnt = parts[1].replace("\\040", " ")
        fs = parts[2]
        if target == mnt or target.startswith(mnt.rstrip("/") + "/"):
            if len(mnt) > best_len:
                best_len = len(mnt)
                fstype = fs
    low = fstype.lower()
    if low in _UNSAFE_FS or low.startswith("fuse"):
        return "DELETE"
    return "WAL"


def configure_connection(conn: sqlite3.Connection, db_path: Path) -> None:
    try:
        conn.execute("PRAGMA busy_timeout=30000")
    except sqlite3.Error:
        pass
    mode = journal_mode_for(db_path)
    try:
        row = conn.execute(f"PRAGMA journal_mode={mode}").fetchone()
        actual = (row[0] if row else mode).upper()
        if mode == "DELETE" and actual == "WAL":
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("PRAGMA journal_mode=DELETE")
    except sqlite3.Error:
        pass
    try:
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        pass
