#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adjust deepseek-v4-flash pool in NewAPI:
- ch118 seeseed     -> main (pri 51)
- ch180 agentrouter -> demote (pri 30) as backup

Uses the SQLite direct-write precedent (docs/ops/archive/
newapi-deepseek-v4-flash-pool-2026-10-08.md): this fork's channel PUT round-trips
`key: ""` from GET and wipes the real key, so priority changes go through
UPDATE channels/abilities only, DB backed up first. channel_cache syncs within
~60s, no restart needed.

abilities is scoped by model, never by channel_id alone: ch118 carries 6 model
rows and a channel-wide sweep would promote its dead legs to head (the 2026-10-09
连坐 bug, see docs/ops/omp-driftfix-dsv4f-head-2026-10-10.md).

--dry-run prints the plan without writing.

Precondition: probe_deepseek_v4_flash_head.py must endorse ch118 before running
this. Re-measured 2026-10-11 00:32 it was 0/6 with the same
`upstream error: do request failed`, so the plan below is on hold; promoting a
channel with auto_ban=0 puts a permanent failing hop in front of every request.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import NoReturn

HOME = Path.home()
DB_PATH = HOME / ".new-api-local" / "new-api.db"
BACKUP_DIR = HOME / ".new-api-local" / "backups"

# channel_id -> target priority
CHANGES = {
    118: 51,  # seeseed: main
    180: 30,  # agentrouter: backup
}
POOL_MODEL = "deepseek-v4-flash"
BACKUP_PREFIX = "new-api-before-deepseek-pool-adjust"


def _redact(text: str) -> str:
    if not text:
        return text
    return re.sub(r"\b(?:sk-|gsk-|pk-|tok-|5jfnxhMjK5E)[A-Za-z0-9_-]{10,}\b", "[REDACTED]", text)


def log(msg: str) -> None:
    print(_redact(msg), flush=True)


def fatal(msg: str) -> NoReturn:
    log(f"ERROR: {msg}")
    sys.exit(1)


def backup_db(src: sqlite3.Connection) -> Path:
    """Online backup: a bare file copy of this WAL-mode DB can miss in-flight pages."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"{BACKUP_PREFIX}-{ts}.db"
    with closing(sqlite3.connect(dest.as_posix())) as dst:
        src.backup(dst)
    return dest


def channel_row(db: sqlite3.Connection, cid: int) -> dict | None:
    row = db.execute(
        "SELECT id, name, status, auto_ban, priority, weight FROM channels WHERE id = ?",
        (cid,),
    ).fetchone()
    if not row:
        return None
    keys = ("id", "name", "status", "auto_ban", "priority", "weight")
    return dict(zip(keys, row))


def ability_priorities(db: sqlite3.Connection, cid: int) -> dict[tuple[str, str], int]:
    rows = db.execute(
        'SELECT model, "group", priority FROM abilities WHERE channel_id = ?',
        (cid,),
    ).fetchall()
    return {(model, group): priority for model, group, priority in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print plan only, do not write")
    args = parser.parse_args()

    if not DB_PATH.exists():
        fatal(f"DB not found: {DB_PATH}")

    with closing(sqlite3.connect(DB_PATH.as_posix(), timeout=30)) as db:
        before = {}
        for cid, want_pri in CHANGES.items():
            cur = channel_row(db, cid)
            if not cur:
                fatal(f"channel {cid} not found")
            ab = ability_priorities(db, cid)
            before[cid] = ab
            touched = [f"{model}/{group}" for (model, group) in ab if model == POOL_MODEL]
            untouched = {f"{model}/{group}": pri for (model, group), pri in ab.items() if model != POOL_MODEL}
            log(f"ch{cid} {cur['name']}: priority {cur['priority']} -> {want_pri} "
                f"(status={cur['status']} auto_ban={cur['auto_ban']} weight={cur['weight']})")
            log(f"  {POOL_MODEL} rows in scope: {touched or 'NONE'}")
            log(f"  rows left untouched: {untouched or 'none'}")

        if args.dry_run:
            log("dry-run only, nothing written.")
            return 0

        backup = backup_db(db)
        log(f"backup: {backup.name}")

        db.execute("BEGIN IMMEDIATE")
        try:
            for cid, want_pri in CHANGES.items():
                db.execute("UPDATE channels SET priority = ? WHERE id = ?", (want_pri, cid))
                cur = db.execute(
                    "UPDATE abilities SET priority = ? WHERE channel_id = ? AND model = ?",
                    (want_pri, cid, POOL_MODEL),
                )
                if cur.rowcount == 0:
                    raise RuntimeError(f"ch{cid}: no {POOL_MODEL} ability row updated")
                log(f"ch{cid}: abilities {POOL_MODEL} rows updated={cur.rowcount}")
            db.commit()
        except Exception as exc:
            db.rollback()
            fatal(f"write failed, rolled back (backup {backup.name}): {exc}")

        for cid, want_pri in CHANGES.items():
            after_ch = channel_row(db, cid)
            after_ab = ability_priorities(db, cid)
            log(f"ch{cid} {after_ch['name']}: priority={after_ch['priority']} abilities={after_ab}")
            if after_ch["priority"] != want_pri:
                fatal(f"ch{cid} channel priority verification failed; restore from {backup.name}")
            for key, pri in after_ab.items():
                model = key[0]
                if model == POOL_MODEL and pri != want_pri:
                    fatal(f"ch{cid} {key} priority={pri}, want {want_pri}; restore from {backup.name}")
                if model != POOL_MODEL and pri != before[cid].get(key):
                    fatal(f"ch{cid} collateral damage on {key}: {before[cid].get(key)} -> {pri}; "
                          f"restore from {backup.name}")

    log(f"DONE. channel_cache syncs within ~60s; verify with /v1/chat/completions {POOL_MODEL}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
