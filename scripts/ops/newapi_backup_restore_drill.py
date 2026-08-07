#!/usr/bin/env python3
"""Monthly NewAPI backup restore drill (read-only).

Copies the newest daily backup to a temp dir, runs SQLite integrity_check,
and samples key tables to prove the backup can actually be opened and read.
Never touches the live new-api.db or the backups directory contents.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

BACKUP_DIR = Path.home() / ".new-api-local" / "backups"
MAX_AGE_DAYS = 3  # drill should always run against a fresh daily backup
DAILY_BACKUP_RE = re.compile(r"^new-api-\d{4}-\d{2}-\d{2}\.db$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=str(BACKUP_DIR), help="backup directory")
    parser.add_argument("--max-age-days", type=int, default=MAX_AGE_DAYS)
    args = parser.parse_args()

    backup_dir = Path(args.dir)
    candidates = sorted(
        (p for p in backup_dir.glob("new-api-*.db") if DAILY_BACKUP_RE.match(p.name)),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        print(f"FAIL: no backups found in {backup_dir}")
        return 1

    newest = candidates[0]
    age = datetime.now() - datetime.fromtimestamp(newest.stat().st_mtime)
    print(f"backup: {newest.name} ({newest.stat().st_size} bytes, age {age.days}d {age.seconds // 3600}h)")

    if age > timedelta(days=args.max_age_days):
        print(f"FAIL: backup older than {args.max_age_days} days; daily backup may be broken")
        return 1

    with tempfile.TemporaryDirectory(prefix="newapi-restore-drill-") as tmpdir:
        copy = Path(tmpdir) / newest.name
        shutil.copy2(newest, copy)
        print(f"copied to temp: {copy}")

        try:
            conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            print(f"FAIL: cannot open backup copy: {exc}")
            return 1

        try:
            check = conn.execute("pragma integrity_check").fetchone()[0]
            print(f"integrity_check: {check}")
            if check != "ok":
                print("FAIL: integrity check did not return ok")
                return 1

            tables = [r[0] for r in conn.execute(
                "select name from sqlite_master where type='table' order by name"
            ).fetchall()]
            print(f"tables: {len(tables)}")

            required = {"channels", "abilities", "options", "logs"}
            missing = required - set(tables)
            if missing:
                print(f"FAIL: missing expected tables: {sorted(missing)}")
                return 1

            for table in ("channels", "options"):
                count = conn.execute(f"select count(*) from {table}").fetchone()[0]
                print(f"{table}: {count} rows")
            conn.close()
        except sqlite3.Error as exc:
            print(f"FAIL: backup copy read error: {exc}")
            return 1

    print("OK: backup restore drill passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
