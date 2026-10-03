#!/usr/bin/env python3
"""Unify the two LongCat 2.5 Preview sources into one aggregated, failover route (2026-10-03).

Background
----------
ch133 `opencode-go-longcat-2.5-preview-free` (opencode-go relay) and ch145
`longcat-official` (official api.longcat.chat) serve the SAME underlying model
under two different gateway ids. User correction: treat as one model ->
aggregate both sources behind BOTH ids with channel-level model_mapping:

- ch133 gains `LongCat-2.5-Preview`  (mapping -> upstream `longcat-2.5-preview-free`)
- ch145 gains `longcat-2.5-preview-free` (mapping -> upstream `LongCat-2.5-Preview`)
- ch145 priority 0 -> 10 = PRIMARY (official; serves only LongCat, no side
  effects); ch133 stays 0 = BACKUP (free relay).

Fork lesson (09-11 memory + agentrouter runbook): this fork rejects
PUT /api/channel updates -> channel model/mapping/priority changes go through
direct sqlite writes (short transaction on the WAL DB), then POST
/api/channel/fix rebuilds abilities. Backup first, guards on expected current
values, idempotent (re-run detects already-unified state and skips writes).

Verification is mapping-specific, non-disruptive:
1. abilities: 4 rows (2 channels x 2 ids) with correct priorities;
2. admin test per channel with the MAPPED id (exercises each new mapping
   through the fork without touching routing);
3. gateway chat per id -> attribution (primary ch145 expected for both).

Usage: ``python3 unify_longcat_preview_sources.py [--apply]`` (no keys needed;
gateway key read silently from models.yml). Dry-run by default.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
DB_PATH = Path.home() / ".new-api-local" / "new-api.db"
GATEWAY_BASE = "http://127.0.0.1:3002"

CH_RELAY = 133
CH_OFFICIAL = 145
ID_RELAY = "longcat-2.5-preview-free"
ID_OFFICIAL = "LongCat-2.5-Preview"
OFFICIAL_PRIORITY = 10


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load smoke helper: {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def db_one(sql, params=(), writable=False):
    uri = f"file:{DB_PATH.as_posix()}{'' if writable else '?mode=ro'}"
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as c:
        return c.execute(sql, params).fetchone()


def db_all(sql, params=()):
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as c:
        return c.execute(sql, params).fetchall()


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        f"new-api-before-longcat-unify-{time.strftime('%Y%m%d-%H%M%S')}.db"
    )
    source = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        target = sqlite3.connect(destination, timeout=30)
        try:
            source.backup(target)
            result = target.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError(f"backup integrity check failed: {result}")
        finally:
            target.close()
    finally:
        source.close()
    return destination


def read_gateway_key() -> str:
    """OMP zg-newapi apiKey from live models.yml (never printed)."""
    text = (Path.home() / ".omp" / "agent" / "models.yml").read_text(encoding="utf-8")
    match = re.search(r"^  zg-newapi:\n(?:    .*\n)*?    apiKey:\s*(\S+)", text, flags=re.M)
    if not match:
        raise RuntimeError("zg-newapi apiKey not found in live models.yml")
    return match.group(1)


def channel_row(cid):
    return db_one(
        "SELECT id, name, status, priority, weight, models, model_mapping FROM channels WHERE id = ?",
        (cid,),
    )


def expected_state():
    """0 = pre-unify, 1 = unified. Raises on drift."""
    relay = channel_row(CH_RELAY)
    official = channel_row(CH_OFFICIAL)
    if not relay or not official:
        raise RuntimeError("ch133/ch145 missing")
    unified = (
        ID_OFFICIAL in str(relay[5]).split(",")
        and ID_RELAY in str(official[5]).split(",")
        and int(official[3]) == OFFICIAL_PRIORITY
    )
    if unified:
        return 1
    if relay[5] != ID_RELAY or official[5] != ID_OFFICIAL or int(official[3]) != 0:
        raise RuntimeError(f"unexpected channel state: ch133={relay[5]!r}/{relay[6]!r} "
                           f"ch145={official[5]!r}/{official[6]!r} prio={official[3]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    args = parser.parse_args()

    print("plan:")
    print(f"  ch{CH_RELAY} += {ID_OFFICIAL} (mapping -> {ID_RELAY})")
    print(f"  ch{CH_OFFICIAL} += {ID_RELAY} (mapping -> {ID_OFFICIAL}), priority 0 -> {OFFICIAL_PRIORITY} (primary)")
    print("  channel/fix -> abilities 4 rows; admin test per mapped id; gateway probe per id")

    state = expected_state()
    if state == 1:
        print("already unified: skipping writes")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    if state == 0:
        backup = online_backup(DB_PATH)
        print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")
        with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}", uri=True, timeout=30)) as c:
            c.execute(
                "UPDATE channels SET models = ?, model_mapping = ? WHERE id = ?",
                (f"{ID_RELAY},{ID_OFFICIAL}",
                 json.dumps({ID_OFFICIAL: ID_RELAY}, separators=(",", ":")),
                 CH_RELAY),
            )
            c.execute(
                "UPDATE channels SET models = ?, model_mapping = ?, priority = ? WHERE id = ?",
                (f"{ID_OFFICIAL},{ID_RELAY}",
                 json.dumps({ID_RELAY: ID_OFFICIAL}, separators=(",", ":")),
                 OFFICIAL_PRIORITY, CH_OFFICIAL),
            )
            c.commit()
        print("db updated: ch133/ch145 models+model_mapping, ch145 priority=10")

    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/fix", method="POST", body={}, headers=headers
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"channel fix failed: HTTP {status} message={message!r}")

    # 1. abilities: 4 rows with correct shapes
    deadline = time.monotonic() + 60
    expected = {
        (ID_OFFICIAL, CH_OFFICIAL, OFFICIAL_PRIORITY),
        (ID_OFFICIAL, CH_RELAY, 0),
        (ID_RELAY, CH_OFFICIAL, OFFICIAL_PRIORITY),
        (ID_RELAY, CH_RELAY, 0),
    }
    while True:
        rows = db_all(
            "SELECT model, channel_id, priority FROM abilities "
            "WHERE channel_id IN (?, ?) AND model IN (?, ?) AND enabled = 1",
            (CH_RELAY, CH_OFFICIAL, ID_RELAY, ID_OFFICIAL),
        )
        got = {(m, int(cid), int(p)) for m, cid, p in rows}
        if got == expected:
            break
        if time.monotonic() > deadline:
            raise RuntimeError(f"abilities shape wrong after fix: {sorted(got)} != {sorted(expected)}")
        time.sleep(2)
    print(f"verify ok: abilities 4 rows {sorted(got)}")

    # 2. admin test per channel with the MAPPED id (exercises each new mapping)
    for cid, model in ((CH_RELAY, ID_OFFICIAL), (CH_OFFICIAL, ID_RELAY)):
        t_status, t_body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/test/{cid}?model={model}",
            headers=headers, timeout=180,
        )
        ok = t_status == 200 and isinstance(t_body, dict) and t_body.get("success")
        if not ok:
            raise RuntimeError(
                f"admin test failed ch{cid} model={model}: HTTP {t_status} {str(t_body)[:200]}"
            )
        print(f"admin test ok: ch{cid} serves {model} (mapping works)")

    # 3. gateway chat per id -> attribution (primary ch145 expected)
    gateway_key = read_gateway_key()
    for model in (ID_OFFICIAL, ID_RELAY):
        mark = time.time()
        status, body = smoke.http_json(
            f"{GATEWAY_BASE}/v1/chat/completions",
            method="POST",
            body={
                "model": model,
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "max_tokens": 512,
            },
            headers={"Authorization": f"Bearer {gateway_key}"},
            timeout=180,
        )
        if status != 200:
            raise RuntimeError(f"gateway chat {model} failed: HTTP {status}: {json.dumps(body)[:200]}")
        row = db_one(
            "SELECT channel_id FROM logs WHERE model_name = ? AND created_at > ? AND type = 2 "
            "ORDER BY id DESC LIMIT 1",
            (model, mark),
        )
        attr = int(row[0]) if row else None
        usage = body.get("usage") or {}
        print(
            f"gateway {model}: 200 usage={usage.get('prompt_tokens')}/{usage.get('completion_tokens')} "
            f"attributed ch{attr} ({'primary' if attr == CH_OFFICIAL else 'backup' if attr == CH_RELAY else 'unexpected!'})"
        )

    print("DONE: LongCat 2.5 Preview unified — official primary, free-relay backup, both ids live")
    return 0


if __name__ == "__main__":
    sys.exit(main())
