from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

SUPERVISOR = Path.home() / ".omp" / "guardian" / "proxies-supervisor.py"
spec = importlib.util.spec_from_file_location("proxies_supervisor_cleanup", SUPERVISOR)
assert spec and spec.loader
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)

SCHEMA = (
    "CREATE TABLE user_sessions ("
    "sid varchar(64) PRIMARY KEY, user_id integer NOT NULL, version bigint NOT NULL DEFAULT 1,"
    "user_auth_version bigint NOT NULL, status varchar(16) NOT NULL, refresh_hash char(64) NOT NULL,"
    "previous_refresh_hash varchar(64), previous_valid_until bigint NOT NULL DEFAULT 0,"
    "login_method varchar(32) NOT NULL, ip varchar(64), user_agent text,"
    "created_at integer, last_active_at bigint NOT NULL, expires_at bigint NOT NULL,"
    "revoked_at bigint NOT NULL DEFAULT 0, revoked_reason varchar(64))"
)


class SessionCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db = Path(self.tempdir.name) / "test.db"
        supervisor.NEWAPI_DB = self.db
        conn = sqlite3.connect(str(self.db))
        conn.execute(SCHEMA)
        now = int(time.time())
        for i in range(30):  # 30 条已过期
            conn.execute(
                "INSERT INTO user_sessions (sid, user_id, user_auth_version, status, refresh_hash, login_method, last_active_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
                (f"expired-{i}", 1, 1, "active", "x" * 64, "password", now - 10000 - i, now - 100),
            )
        for i in range(30):  # 30 条未过期，last_active 递增
            conn.execute(
                "INSERT INTO user_sessions (sid, user_id, user_auth_version, status, refresh_hash, login_method, last_active_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
                (f"live-{i:02d}", 1, 1, "active", "y" * 64, "password", now - i, now + 100000),
            )
        conn.commit()
        conn.close()

    def test_cleanup_removes_expired_and_keeps_most_recent(self) -> None:
        removed_expired, removed_overflow = supervisor.cleanup_user_sessions()
        self.assertEqual(removed_expired, 30)
        self.assertEqual(removed_overflow, 20)
        conn = sqlite3.connect(str(self.db))
        remaining = conn.execute("SELECT sid FROM user_sessions ORDER BY last_active_at DESC").fetchall()
        conn.close()
        self.assertEqual(len(remaining), 10)
        self.assertEqual([r[0] for r in remaining], [f"live-{i:02d}" for i in range(10)])

    def test_cleanup_is_idempotent(self) -> None:
        supervisor.cleanup_user_sessions()
        expired, overflow = supervisor.cleanup_user_sessions()
        self.assertEqual((expired, overflow), (0, 0))
        conn = sqlite3.connect(str(self.db))
        self.assertEqual(conn.execute("SELECT count(*) FROM user_sessions").fetchone()[0], 10)
        conn.close()


if __name__ == "__main__":
    unittest.main()
