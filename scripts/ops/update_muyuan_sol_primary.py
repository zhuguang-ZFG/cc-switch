"""Promote the verified muyuan.do Sol channel above AgentRouter."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import time
from pathlib import Path


CHANNEL_ID = 83
TARGET_PRIORITY = 50
TARGET_WEIGHT = 5


def load_smoke():
    path = Path(__file__).with_name("newapi-local-smoke.py")
    spec = importlib.util.spec_from_file_location("newapi_local_smoke_muyuan", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load newapi-local-smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_update(channel: dict) -> dict:
    if channel.get("id") != CHANNEL_ID or channel.get("name") != "muyuan-sol":
        raise RuntimeError("expected channel id 83 named muyuan-sol")
    key = channel.get("key")
    if not isinstance(key, str) or not key or "*" in key:
        raise RuntimeError("channel key is empty or masked")
    updated = {name: value for name, value in channel.items() if name != "status"}
    updated["priority"] = TARGET_PRIORITY
    updated["weight"] = TARGET_WEIGHT
    return updated

def hydrate_channel_key(channel: dict, smoke) -> dict:
    key = channel.get("key")
    if isinstance(key, str) and key.strip() and "*" not in key:
        return channel
    con = sqlite3.connect(f"file:{Path(smoke.NEWAPI_DB).as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT key FROM channels WHERE id = ?",
            (CHANNEL_ID,),
        ).fetchone()
    finally:
        con.close()
    actual = row[0] if row else None
    if not isinstance(actual, str) or not actual.strip():
        raise RuntimeError("channel key unavailable in local SSOT")
    return {**channel, "key": actual}


def backup_database(source_path: Path, destination: Path) -> None:
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"database backup integrity check failed: {result}")
    finally:
        target.close()
        source.close()


def read_ability_rows(db_path: Path) -> list[tuple[str, int, int, int]]:
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as con:
        return list(
            con.execute(
                "SELECT model, enabled, priority, weight FROM abilities "
                "WHERE channel_id = ? ORDER BY model",
                (CHANNEL_ID,),
            )
        )


def verify(channel: object, ability_rows: list[tuple[str, int, int, int]], smoke) -> bool:
    if not isinstance(channel, dict):
        return False
    if smoke.muyuan_sol_primary_violations([channel]):
        return False
    expected_models = set(smoke.MUYUAN_SOL_PRIMARY_CONTRACT["models"])
    actual = {
        model: (enabled, priority, weight)
        for model, enabled, priority, weight in ability_rows
        if model in expected_models
    }
    return actual == {
        model: (1, TARGET_PRIORITY, TARGET_WEIGHT)
        for model in expected_models
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{CHANNEL_ID}", headers=headers
    )
    channel = body.get("data") if isinstance(body, dict) else None
    if status != 200 or not isinstance(channel, dict):
        print(f"channel read failed: HTTP {status}")
        return 1
    try:
        channel = hydrate_channel_key(channel, smoke)
        updated = build_update(channel)
    except RuntimeError as error:
        print(error)
        return 1

    print(
        f"current=priority:{channel.get('priority')},weight:{channel.get('weight')} "
        f"proposed=priority:{TARGET_PRIORITY},weight:{TARGET_WEIGHT}"
    )
    if channel.get("priority") == TARGET_PRIORITY and channel.get("weight") == TARGET_WEIGHT:
        rows = read_ability_rows(Path(smoke.NEWAPI_DB))
        if verify(channel, rows, smoke):
            print("already configured")
            return 0
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup_dir = Path(smoke.DEPLOY_DIR) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"new-api-before-muyuan-sol-primary-{time.strftime('%Y%m%d-%H%M%S')}.db"
    backup_database(Path(smoke.NEWAPI_DB), backup)

    put_status, put_body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/",
        method="PUT",
        body=updated,
        headers=headers,
    )
    if put_status != 200 or not isinstance(put_body, dict) or not put_body.get("success"):
        print(f"update failed: HTTP {put_status}; backup={backup.name}")
        return 1

    verify_status, verify_body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{CHANNEL_ID}", headers=headers
    )
    verified = verify_body.get("data") if isinstance(verify_body, dict) else None
    rows = read_ability_rows(Path(smoke.NEWAPI_DB))
    ok = verify_status == 200 and verify(verified, rows, smoke)
    print(f"backup={backup.name} verified={ok}")
    if ok:
        return 0

    rollback = {name: value for name, value in channel.items() if name != "status"}
    rollback_status, rollback_body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/",
        method="PUT",
        body=rollback,
        headers=headers,
    )
    rollback_ok = (
        rollback_status == 200
        and isinstance(rollback_body, dict)
        and bool(rollback_body.get("success"))
    )
    print(f"verification failed; rollback_ok={rollback_ok}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
