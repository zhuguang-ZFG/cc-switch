"""Regression: the daily snapshot prune must keep the newest *by mtime*.

2026-10-16: `backup_newapi_db` sorted candidate snapshots by file NAME (no
`key=`), so anything whose slug sorted early was deleted as if it were old.
Real damage: `new-api-before-agentrouter-newmodels-20261015-212818.db` and
`new-api-before-ch136-reenable-20261015-222306.db` (both newer than every
survivor) were pruned while 16:12-19:00 snapshots stayed; two runbooks then
cited snapshots that no longer existed. The same bug also deleted the daily
`new-api-<date>.db` right after writing it ('2' sorts before 'b').
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from supervisor_test_support import load_supervisor

supervisor = load_supervisor("proxies_supervisor")


class BackupRotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.backup_dir = self.root / "backups"
        self.backup_dir.mkdir()
        self.db = self.root / "new-api.db"
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE probe (x INTEGER)")
        conn.commit()
        conn.close()

    def _seed(self, name: str, age_s: float) -> Path:
        path = self.backup_dir / name
        path.write_bytes(b"seed")
        stamp = time.time() - age_s
        os.utime(path, (stamp, stamp))
        return path

    def test_prune_keeps_newest_by_mtime_even_when_names_sort_early(self) -> None:
        keep = supervisor.BACKUP_KEEP
        # Name order and mtime order deliberately disagree: 'zzz' is the oldest
        # snapshot and 'aaa' the newest; the old name sort pruned 'aaa' first.
        self._seed("new-api-before-zzz-20260101-000000.db", age_s=10_000)
        self._seed("new-api-before-aaa-20260102-000000.db", age_s=1)
        for index in range(keep - 1):
            self._seed(f"new-api-before-m{index:02d}-20260101-000000.db", age_s=5_000 - index)

        with (
            patch.object(supervisor, "BACKUP_DIR", self.backup_dir),
            patch.object(supervisor, "NEWAPI_DB", self.db),
            patch.object(supervisor, "log"),
        ):
            supervisor.backup_newapi_db("2026-10-16")

        names = sorted(path.name for path in self.backup_dir.glob("*.db"))
        self.assertEqual(len(names), keep)
        self.assertIn("new-api-2026-10-16.db", names)
        self.assertIn("new-api-before-aaa-20260102-000000.db", names)
        self.assertNotIn("new-api-before-zzz-20260101-000000.db", names)


if __name__ == "__main__":
    unittest.main()
