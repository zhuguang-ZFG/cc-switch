#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Repair channels whose test_model is a model the channel does not carry.

Found while chasing the linxi ch9/ch18 disable on 2026-10-11: ch94/ch95
(justwoker-opus-1/2) advertise models=claude-opus-4-8 but test_model=claude-opus-5.
Every scheduled and Guardian-initiated test therefore asked the relay for a model it
does not serve, got `503 No available channel for model claude-opus-5`, and with
auto_ban=1 the two channels stayed pinned out of the pool — while testing them on
their real model returned 3/3 in ~4s each. They were the only live Claude legs.
Re-used 2026-10-11 for ch178 zen-free-bridge, whose test_model fledge-alpha-free is
not in the opencode catalogue at all (0/1 HTTP 502 vs 2/2 on big-pickle).

test_model is channels-only metadata, so it goes through a direct UPDATE (this fork's
channel PUT round-trips key:"" back out of GET and wipes the stored key). Enabling an
auto-banned channel must use POST /api/channel/{id}/status; a PUT carrying status is
rejected with 'Invalid parameters'.

Usage: fix_channel_test_model.py --set 94=claude-opus-4-8 [--set ...] [--enable 94 ...]
--dry-run prints the plan without writing.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import NoReturn

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
DB_PATH = Path.home() / ".new-api-local" / "new-api.db"
BACKUP_PREFIX = "new-api-before-test-model-fix"

# channel_id -> corrected test_model, filled from --set CID=MODEL
FIXES: dict[int, str] = {}
# auto-banned legs to re-enable after the correction, filled from --enable CID
ENABLE: list[int] = []


def fatal(msg: str) -> NoReturn:
    print(f"ERROR: {msg}", flush=True)
    sys.exit(1)


def log(msg: str) -> None:
    print(msg, flush=True)


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        fatal(f"cannot load smoke module from {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def carried_models(row: sqlite3.Row) -> set[str]:
    owned = {m.strip() for m in (row["models"] or "").split(",") if m.strip()}
    try:
        owned |= set(json.loads(row["model_mapping"] or "{}").keys())
    except (ValueError, TypeError):
        pass
    return owned


def fetch(db: sqlite3.Connection, cid: int) -> sqlite3.Row | None:
    return db.execute(
        "SELECT id, name, status, auto_ban, priority, weight, test_model, models, model_mapping"
        " FROM channels WHERE id = ?",
        (cid,),
    ).fetchone()


def backup_db(src: sqlite3.Connection) -> Path:
    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"{BACKUP_PREFIX}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    with closing(sqlite3.connect(dest.as_posix())) as dst:
        src.backup(dst)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print plan only, do not write")
    parser.add_argument("--set", dest="sets", metavar="CID=MODEL", action="append", required=True,
                        help="correct channel CID test_model to MODEL (repeatable)")
    parser.add_argument("--enable", dest="enables", metavar="CID", action="append", default=[],
                        help="re-enable this channel after correcting it (repeatable)")
    args = parser.parse_args()

    for spec in args.sets:
        cid_text, _, model = spec.partition("=")
        if not model:
            fatal(f"--set needs CID=MODEL, got {spec!r}")
        FIXES[int(cid_text)] = model
    for cid_text in args.enables:
        cid = int(cid_text)
        if cid not in FIXES:
            fatal(f"--enable {cid} has no matching --set; cannot verify it on a corrected model")
        ENABLE.append(cid)

    if not DB_PATH.exists():
        fatal(f"DB not found: {DB_PATH}")

    with closing(sqlite3.connect(DB_PATH.as_posix(), timeout=30)) as db:
        db.row_factory = sqlite3.Row

        for cid, want in FIXES.items():
            row = fetch(db, cid)
            if row is None:
                fatal(f"channel {cid} not found")
            owned = carried_models(row)
            if want not in owned:
                fatal(f"ch{cid} {row['name']}: target test_model {want!r} not carried by {sorted(owned)}")
            log(f"ch{cid} {row['name']}: test_model {row['test_model']!r} -> {want!r} "
                f"(status={row['status']} auto_ban={row['auto_ban']} p={row['priority']} w={row['weight']})")

        if args.dry_run:
            log("dry-run only, nothing written.")
            return 0

        backup = backup_db(db)
        log(f"backup: {backup.name}")

        db.execute("BEGIN IMMEDIATE")
        try:
            for cid, want in FIXES.items():
                cur = db.execute("UPDATE channels SET test_model = ? WHERE id = ?", (want, cid))
                if cur.rowcount != 1:
                    raise RuntimeError(f"ch{cid}: expected 1 row, touched {cur.rowcount}")
            db.commit()
        except Exception as exc:
            db.rollback()
            fatal(f"test_model write failed, rolled back (backup {backup.name}): {exc}")

        for cid, want in FIXES.items():
            got = db.execute("SELECT test_model FROM channels WHERE id = ?", (cid,)).fetchone()[0]
            if got != want:
                fatal(f"ch{cid} test_model verification failed ({got!r}); restore from {backup.name}")
        log("test_model corrected and verified.")

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    for cid in ENABLE:
        model = FIXES[cid]
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/{cid}/status",
            method="POST", body={"status": 1}, headers=headers, timeout=30,
        )
        log(f"ch{cid} enable: HTTP {status} success={body.get('success')} msg={str(body.get('message'))[:120]}")

        # the channel cache needs ~60s; poll the real test on the corrected model
        verdict = None
        for attempt in range(1, 7):
            time.sleep(15)
            t_status, t_body = smoke.http_json(
                f"{smoke.NEWAPI_BASE}/api/channel/test/{cid}?model={model}",
                headers=headers, timeout=120,
            )
            verdict = t_body.get("success")
            log(f"ch{cid} test [{attempt}/6] model={model} success={verdict} "
                f"time={t_body.get('time')}ms msg={str(t_body.get('message'))[:120]}")
            if verdict:
                break

        live = smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/{cid}", headers=headers, timeout=30)[1].get("data") or {}
        log(f"ch{cid} state: status={live.get('status')} test_model={live.get('test_model')!r} "
            f"auto_ban={live.get('auto_ban')}")
        if not verdict or live.get("status") != 1:
            fatal(f"ch{cid} did not come up (status={live.get('status')} test={verdict}); "
                  f"restore from {backup.name} if unwanted")

    log(f"DONE. rollback backup: {backup.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
