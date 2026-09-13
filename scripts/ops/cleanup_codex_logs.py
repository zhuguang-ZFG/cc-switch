#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bound Codex CLI diagnostic-log growth (runbook 2026-09-13: logs_2.sqlite
278MB / 46404 rows, 78% written within 24h -- codex 0.154.0 log storm).

Scope: ~/.codex/logs_*.sqlite `logs` tables ONLY. These are diagnostic events
codex never reads back for functionality; thread resume lives in
thread_history_*.sqlite, which this script NEVER touches.

Keeps the newest --keep-days days; VACUUM is best-effort (needs an exclusive
lock, silently skipped while codex is running). Dry-run with --dry-run.
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

CODEX_HOME = Path.home() / ".codex"


MIB = 1024 * 1024


def process(db_path: Path, keep_days: int, dry_run: bool) -> int:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5)
    max_ts = conn.execute("SELECT COALESCE(MAX(ts), 0) FROM logs").fetchone()[0]
    conn.close()
    # ts is epoch microseconds when large, seconds otherwise (runbook: the
    # thread_history tables use seconds; logs observed in the micro range)
    if max_ts > 100_000_000_000_000:
        cutoff = (time.time() - keep_days * 86400) * 1_000_000
    else:
        cutoff = time.time() - keep_days * 86400

    before = db_path.stat().st_size
    if dry_run:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        stale = conn.execute("SELECT COUNT(*) FROM logs WHERE ts < ?", (cutoff,)).fetchone()[0]
        conn.close()
        print(f"[dry-run] {db_path.name}: would delete {stale} rows of "
              f"{before / MIB:.1f} MB")
        return 0

    conn = sqlite3.connect(db_path.as_posix(), timeout=15)
    try:
        conn.execute("PRAGMA busy_timeout=15000")
        cur = conn.execute("DELETE FROM logs WHERE ts < ?", (cutoff,))
        conn.commit()
        try:
            conn.execute("VACUUM")
        except sqlite3.Error:
            pass  # exclusive lock unavailable while codex runs; next run catches it
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    after = db_path.stat().st_size
    freed = before - after
    print(f"{db_path.name}: deleted {cur.rowcount} rows, "
          f"{before / MIB:.1f} -> {after / MIB:.1f} MB")
    return max(0, freed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-days", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    total = 0
    for db in sorted(CODEX_HOME.glob("logs_*.sqlite")):
        total += process(db, args.keep_days, args.dry_run)
    print(f"freed {total / MIB:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
