from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("refresh_omp_sota_readiness.py")
SPEC = importlib.util.spec_from_file_location("refresh_omp_sota_readiness", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
refresh = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(refresh)


class RefreshOmpSotaReadinessTests(unittest.TestCase):
    def test_discovers_only_the_isolated_marked_channel(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "new-api.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "CREATE TABLE channels (id INTEGER, name TEXT, models TEXT)"
                )
                connection.executemany(
                    "INSERT INTO channels VALUES (?, ?, ?)",
                    [
                        (75, "tabitoken", "claude-opus-5,omp-sota-claude-opus-5"),
                        (93, "omp-sota-sotamodel", "claude-opus-5,omp-sota-claude-opus-5"),
                    ],
                )
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(refresh.isolated_channel_id(database), 93)

    def test_refuses_ambiguous_isolated_channels(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "new-api.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "CREATE TABLE channels (id INTEGER, name TEXT, models TEXT)"
                )
                connection.executemany(
                    "INSERT INTO channels VALUES (?, ?, ?)",
                    [
                        (93, "omp-sota-a", refresh.MODEL),
                        (94, "omp-sota-b", refresh.MODEL),
                    ],
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(RuntimeError, "multiple isolated"):
                refresh.isolated_channel_id(database)


if __name__ == "__main__":
    unittest.main()
