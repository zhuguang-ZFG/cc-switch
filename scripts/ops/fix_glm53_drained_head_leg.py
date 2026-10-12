#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Take the drained clinepass leg out of glm-5.3's head tier and make sheds fail over.

Measured 2026-10-11 20:35-20:50, all three facts reproduced before this script existed:

1. OMP's `zg-newapi` provider key is NewAPI token t1 `master`, group `default`, so every
   OMP role on `glm-5.3` (plan/designer/task/advisor) distributes inside group default.
2. In that group glm-5.3 had exactly three enabled legs: ch203 clinepass p30 w3,
   ch208 happycoding p30 w1, ch209 qgyz13 p20 w1. ch203's upstream account is on a weekly
   cap -- `Error 429: You have reached your weekly Clinepass limit. The limit resets in
   4d 17h` -- reproduced on glm-5.3, minimax-m3 and deepseek-v4.1-flash, so it is
   account-wide across all 12 names, not a per-model problem. Three of every four draws
   therefore landed on a leg that could not answer.
3. That draw is a hard client failure, not a failover. The gateway retries only inside
   AutomaticRetryStatusCodes (408,500-503) and ch203 carries an empty status_code_mapping,
   so 429 reaches the caller: `POST /v1/chat/completions | 429 | 1.14s` with a single
   `channel error (channel #203, status code: 429)` and no retry line. Widening the global
   budget is deliberately off limits -- update_newapi_retry_budget.py converged it to one
   retry on those codes to stop retry storms -- so the fix is per channel: map the shed
   codes to 503 the way configure_codex_agent_any.py already does for ch126/ch127.

Cross-channel retry does exist and does descend tiers (220 retry lines today: 152 re-picks
of the same channel, which only happen where it is the sole leg, and 68 moves including
127->175 and 127->182, which sit two tiers below it), so demoting ch203 keeps it reachable
as a last resort instead of deleting its capacity.

Writes, all of them channels/abilities only, no channel PUT (this fork's PUT round-trips
`key: ""` and wipes the credential):
  ch203 priority 30 -> 10 on the channel and every one of its abilities rows, because the
      cap is account-wide. The 连坐 hazard in adjust_deepseek_v4_flash_pool.py is promoting
      dead rows to head with a channel-wide sweep; this sweep only demotes.
  ch209 priority 20 -> 30 so the head tier is two live upstreams sharing 50/50 rather than
      happycoding alone.
  ch203/ch208/ch209 status_code_mapping <- {"402": "503", "403": "503", "429": "503"} so a
      shed on any of them costs one extra hop instead of a client-visible failure.

--dry-run prints the plan. --restore reverses to the posture measured at 20:35.
"""
from __future__ import annotations

import argparse
import json
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
BACKUP_PREFIX = "new-api-before-glm53-pool"

POOL_MODEL = "glm-5.3"
DEMOTE = {203: 10}
PROMOTE = {209: 30}
KEEP = (208,)
RETRY_MAPPING = {"402": "503", "403": "503", "429": "503"}
TOUCHED = (*DEMOTE, *PROMOTE, *KEEP)
# the posture measured at 2026-10-11 20:35, which --restore goes back to
ORIGINAL_PRIORITY = {203: 30, 208: 30, 209: 20}


def _redact(text: str) -> str:
    if not text:
        return text
    return re.sub(r"\b(?:sk-|gsk-|pk-|tok-)[A-Za-z0-9_-]{10,}\b", "[REDACTED]", text)


def log(msg: str) -> None:
    print(_redact(msg), flush=True)


def fatal(msg: str) -> NoReturn:
    log(f"ERROR: {msg}")
    sys.exit(1)


def json_column(raw) -> dict:
    """Read a channels JSON column this fork stores as TEXT on some rows and BLOB on others."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def merge_retry_mapping(raw) -> str:
    mapping = json_column(raw)
    if not isinstance(mapping, dict):
        fatal(f"status_code_mapping is not a JSON object: {str(raw)[:80]!r}")
    for code, target in RETRY_MAPPING.items():
        current = str(mapping.get(code, ""))
        if current and current != target:
            fatal(f"a human already maps {code} -> {current}; refusing to overwrite that judgment")
        mapping[code] = target
    return json.dumps(mapping, sort_keys=True)


def backup_db(src: sqlite3.Connection) -> Path:
    """Online backup: a bare file copy of this WAL-mode DB can miss in-flight pages."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / f"{BACKUP_PREFIX}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    with closing(sqlite3.connect(dest.as_posix())) as dst:
        src.backup(dst)
    if not dest.exists() or dest.stat().st_size < 1_000_000:
        fatal(f"backup did not land: {dest}")
    return dest


def channel_rows(db: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    rows = db.execute(
        'SELECT id, name, status, auto_ban, priority, weight, "group", models, '
        "status_code_mapping, test_model FROM channels WHERE id IN (%s)" % ",".join("?" * len(TOUCHED)),
        TOUCHED,
    ).fetchall()
    found = {row["id"]: row for row in rows}
    missing = [cid for cid in TOUCHED if cid not in found]
    if missing:
        fatal(f"channels not found: {missing}")
    return found


def ability_rows(db: sqlite3.Connection, cid: int) -> list[tuple[str, str, int, int]]:
    return [
        (model, group, priority, weight)
        for model, group, priority, weight in db.execute(
            'SELECT model, "group", priority, weight FROM abilities WHERE channel_id = ? ORDER BY model, "group"',
            (cid,),
        )
    ]


def head_tier(db: sqlite3.Connection) -> list[tuple[int, int, int, int]]:
    """Enabled legs at the top priority that actually carries a reply for POOL_MODEL."""
    return [
        (channel_id, group, priority, weight)
        for channel_id, group, priority, weight in db.execute(
            'SELECT a.channel_id, a."group", a.priority, a.weight FROM abilities a '
            "JOIN channels c ON c.id = a.channel_id "
            "WHERE a.model = ? AND a.enabled = 1 AND c.status = 1 ORDER BY a.priority DESC, a.\"group\"",
            (POOL_MODEL,),
        )
    ]


def report(db: sqlite3.Connection, rows: dict[int, sqlite3.Row]) -> None:
    log(f"pool premise: {POOL_MODEL} enabled legs")
    for channel_id, group, priority, weight in head_tier(db):
        log(f"  ch{channel_id:<4}{rows[channel_id]['name'] if channel_id in rows else '?':<18}"
            f"group={group:<8} p={priority:<4} w={weight}")
    for cid in TOUCHED:
        row = rows[cid]
        log(f"ch{cid} {row['name']}: status={row['status']} p={row['priority']} w={row['weight']} "
            f"auto_ban={row['auto_ban']} grp={row['group']} test_model={row['test_model']!r} "
            f"mapping={str(row['status_code_mapping'])[:60]!r}")
        log(f"  abilities rows: {ability_rows(db, cid)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="rebalance the glm-5.3 pool around the drained clinepass leg")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    parser.add_argument("--apply", action="store_true", help="write the rebalanced posture")
    parser.add_argument("--restore", action="store_true", help=f"reverse to the {ORIGINAL_PRIORITY} priorities and drop the added mapping entries")
    args = parser.parse_args()
    if args.apply and args.restore:
        fatal("--apply and --restore are opposite directions")
    if not (args.apply or args.restore or args.dry_run):
        log("default is dry-run; pass --apply to write or --restore to reverse")
    if not DB_PATH.exists():
        fatal(f"DB not found: {DB_PATH}")

    want = {cid: (ORIGINAL_PRIORITY[cid] if args.restore else target) for cid, target in {**DEMOTE, **PROMOTE}.items()}
    with closing(sqlite3.connect(DB_PATH.as_posix(), timeout=30)) as db:
        db.row_factory = sqlite3.Row
        rows = channel_rows(db)
        report(db, rows)

        for cid in TOUCHED:
            if rows[cid]["status"] != 1:
                fatal(f"ch{cid} {rows[cid]['name']} is status={rows[cid]['status']}; this script only rebalances live legs")
        if rows[208]["test_model"] != POOL_MODEL:
            log(f"NOTE: ch208 test_model={rows[208]['test_model']!r}, not {POOL_MODEL!r}")

        if args.restore:
            mappings = {}
            for cid in TOUCHED:
                mapping = json_column(rows[cid]["status_code_mapping"])
                leftovers = {code: target for code, target in mapping.items() if code not in RETRY_MAPPING}
                if any(str(mapping.get(code)) != target for code, target in RETRY_MAPPING.items()):
                    log(f"NOTE: ch{cid} mapping is not what this script writes; removing only its own keys")
                mappings[cid] = json.dumps(leftovers, sort_keys=True) if leftovers else ""
        else:
            mappings = {cid: merge_retry_mapping(rows[cid]["status_code_mapping"]) for cid in TOUCHED}

        log(f"plan: priorities {want}; status_code_mapping "
            f"{'cleared' if args.restore else RETRY_MAPPING}")
        if args.dry_run or not (args.apply or args.restore):
            log("dry-run only, nothing written.")
            return 0

        backup = backup_db(db)
        log(f"backup: {backup.name} ({backup.stat().st_size} bytes)")
        before = {cid: ability_rows(db, cid) for cid in TOUCHED}

        db.execute("BEGIN IMMEDIATE")
        try:
            for cid, priority in want.items():
                if db.execute("UPDATE channels SET priority = ? WHERE id = ?", (priority, cid)).rowcount != 1:
                    raise RuntimeError(f"ch{cid}: channel priority row not found")
                cur = db.execute("UPDATE abilities SET priority = ? WHERE channel_id = ?", (priority, cid))
                if cur.rowcount == 0:
                    raise RuntimeError(f"ch{cid}: no abilities row updated")
                log(f"ch{cid}: priority -> {priority} on {cur.rowcount} abilities rows")
            for cid, mapping in mappings.items():
                if db.execute("UPDATE channels SET status_code_mapping = ? WHERE id = ?", (mapping, cid)).rowcount != 1:
                    raise RuntimeError(f"ch{cid}: mapping row not found")
                log(f"ch{cid}: status_code_mapping -> {mapping or '(cleared)'}")
            db.commit()
        except Exception as exc:
            db.rollback()
            fatal(f"write failed, rolled back (backup {backup.name}): {exc}")

        for cid, priority in want.items():
            got = db.execute("SELECT priority, status_code_mapping FROM channels WHERE id = ?", (cid,)).fetchone()
            if got["priority"] != priority:
                fatal(f"ch{cid} priority verification failed ({got['priority']}); restore from {backup.name}")
            if (got["status_code_mapping"] or "") != mappings[cid]:
                fatal(f"ch{cid} mapping verification failed ({got['status_code_mapping']!r}); restore from {backup.name}")
            after = {(model, group): (row_priority, weight) for model, group, row_priority, weight in ability_rows(db, cid)}
            was = {(model, group): (row_priority, weight) for model, group, row_priority, weight in before[cid]}
            if set(after) != set(was):
                fatal(f"ch{cid} abilities row set changed: {set(after) ^ set(was)}; restore from {backup.name}")
            for key, (row_priority, weight) in after.items():
                if row_priority != priority:
                    fatal(f"ch{cid} {key} priority={row_priority}, want {priority}; restore from {backup.name}")
                if weight != was[key][1]:
                    fatal(f"ch{cid} {key} weight drifted {was[key][1]} -> {weight}; restore from {backup.name}")
            log(f"ch{cid} verified: {sorted(after.items())}")

        log(f"resulting {POOL_MODEL} legs")
        for channel_id, group, priority, weight in head_tier(db):
            log(f"  [{group}] ch{channel_id} p={priority} w={weight}")

    log(f"DONE. channel_cache syncs within ~75s; verify with a gateway request on {POOL_MODEL}.")
    log(f"rollback backup: {backup.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
