#!/usr/bin/env python3
"""Park zombie channels ch127 + ch9 into Guardian's self-healing recovery orbit (2026-10-07).

User-authorized disposition (ch127/ch9/ch148). Upstreams dead at authorization:
- ch127 agentrouter-codex-gpt: gpt-5.6-sol -> 503 无可用渠道; gpt-6-astra -> 402 Budget pool exhaustion
- ch9  linxi-k40:             claude-opus-5 -> 503 No available accounts; claude-fable-5 -> 404 not supported
(ch148 budsin-apichat is ALIVE (admin 200/8s) — intentionally NOT parked.)

Posture: auto_ban=1 (PUT) + status=2 (POST /status). House-style park (ch172
precedent): smoke's disable-attribution treats status=2 with auto_ban=1 as an
intentional park (not flagged), while Guardian keeps its hands off (its import
only takes status=3 && auto_ban=1). Re-enable is manual, per-channel:
  ch127: agentrouter 预算池充值 + sol/astra 上游复测 200 -> POST /status {"status":1}
  ch9:   linxi 账号池回血 + admin 复测 200            -> POST /status {"status":1}
(Fork rejects status=3 via the admin endpoint: "Invalid parameters" 2026-10-07.)
Weight/priority untouched.

dry-run default; snapshot before any write; identity + key hydration from the
local SSOT (channels.key), mirroring quarantine_newapi_channels.py.
"""
from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import time
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
DB_PATH = Path.home() / ".new-api-local" / "new-api.db"

# id -> expected channel name (identity check before any write)
TARGETS = {
    127: "agentrouter-codex-gpt",
    9: "linxi-k40",
}
PARK_STATUS = 2


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load smoke module from {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def usable_key(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "*" not in value


def hydrate_key(channel: dict, db_path: Path) -> dict:
    channel_id = channel.get("id")
    channel_name = channel.get("name")
    if not isinstance(channel_id, int) or not isinstance(channel_name, str):
        raise RuntimeError("channel identity is incomplete")
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        row = connection.execute(
            "SELECT name, key FROM channels WHERE id = ?", (channel_id,)
        ).fetchone()
    if not row or row[0] != channel_name:
        raise RuntimeError(f"channel {channel_id} identity mismatch in local SSOT")
    row_key = row[1]
    if not isinstance(row_key, str) or not usable_key(row_key):
        raise RuntimeError(f"channel {channel_id} key unavailable in local SSOT")
    supplied = channel.get("key")
    if isinstance(supplied, str) and usable_key(supplied) and supplied.strip() != row_key.strip():
        raise RuntimeError(f"channel {channel_id} key mismatch in local SSOT")
    return {**channel, "key": row_key}


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"new-api-before-park-ch127-ch9-{time.strftime('%Y%m%d-%H%M%S')}.db"
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as source:
        with closing(sqlite3.connect(destination, timeout=30)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise RuntimeError("backup integrity check failed")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    args = parser.parse_args()

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    plan = []
    for cid, expected_name in TARGETS.items():
        st, body = smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/{cid}", headers=headers)
        channel = (body or {}).get("data") if isinstance(body, dict) else None
        if st != 200 or not isinstance(channel, dict) or channel.get("name") != expected_name:
            raise RuntimeError(
                f"ch{cid} identity mismatch: HTTP {st} "
                f"name={channel.get('name') if isinstance(channel, dict) else None!r}"
            )
        plan.append(channel)
        print(
            f"plan ch{cid} {expected_name}: status={channel.get('status')} "
            f"p={channel.get('priority')} w={channel.get('weight')} "
            f"auto_ban={channel.get('auto_ban')} -> park (auto_ban=1, status={PARK_STATUS})"
        )
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    for channel in plan:
        cid = int(channel["id"])
        name = str(channel["name"])
        hydrated = hydrate_key(channel, DB_PATH)
        updated = {key: value for key, value in hydrated.items() if key != "status"}
        updated["auto_ban"] = 1
        st, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/", method="PUT", body=updated, headers=headers
        )
        if st != 200 or not isinstance(body, dict) or not body.get("success"):
            raise RuntimeError(f"ch{cid} PUT(auto_ban=1) failed: HTTP {st} body={str(body)[:220]}")
        print(f"ch{cid} {name}: PUT auto_ban=1 ok")
        st, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/{cid}/status",
            method="POST",
            body={"status": PARK_STATUS},
            headers=headers,
        )
        if st != 200 or not isinstance(body, dict) or not body.get("success"):
            raise RuntimeError(f"ch{cid} status={PARK_STATUS} failed: HTTP {st} body={str(body)[:220]}")
        st, body = smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/{cid}", headers=headers)
        after = (body or {}).get("data") or {}
        print(
            f"ch{cid} readback: status={after.get('status')} auto_ban={after.get('auto_ban')} "
            f"p={after.get('priority')} w={after.get('weight')}"
        )
        with closing(
            sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)
        ) as connection:
            rows = connection.execute(
                "SELECT model, enabled FROM abilities WHERE channel_id = ? ORDER BY model",
                (cid,),
            ).fetchall()
        print(f"ch{cid} abilities: {rows}")

    print("DONE: parked (status=2 + auto_ban=1, house-style ch172 park). NEXT: smoke zero-new check + gateway probes; manual re-enable per docstring when upstream heals.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
