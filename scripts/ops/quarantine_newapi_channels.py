"""Back up and double-lock explicitly selected NewAPI channels."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path


def load_smoke():
    path = Path(__file__).with_name("newapi-local-smoke.py")
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load newapi-local-smoke.py")
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
    if not usable_key(row[1]):
        raise RuntimeError(f"channel {channel_id} key unavailable in local SSOT")
    supplied_key = channel.get("key")
    if usable_key(supplied_key) and supplied_key.strip() != row[1].strip():
        raise RuntimeError(f"channel {channel_id} key mismatch in local SSOT")
    return {**channel, "key": row[1]}


def online_backup(db_path: Path, backup_dir: Path, channel_ids: list[int]) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    label = "-".join(str(channel_id) for channel_id in channel_ids)
    backup = backup_dir / f"new-api-before-quarantine-{label}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    if backup.exists():
        raise RuntimeError(f"backup already exists: {backup.name}")
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as source, closing(sqlite3.connect(backup, timeout=30)) as target:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RuntimeError("backup integrity check failed")
    if backup.stat().st_size <= 0:
        raise RuntimeError("backup is empty")
    return backup


def safe_channel_summary(channel: dict) -> dict:
    fields = (
        "id",
        "name",
        "status",
        "priority",
        "weight",
        "auto_ban",
        "test_model",
        "models",
    )
    return {key: channel[key] for key in fields if key in channel}


def read_ability_posture(db_path: Path, channel_id: int) -> list[tuple]:
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        return [
            tuple(row)
            for row in connection.execute(
                "SELECT model, enabled, priority, weight FROM abilities "
                "WHERE channel_id = ? ORDER BY model",
                (channel_id,),
            )
        ]


def verify_quarantined(smoke, headers: dict, db_path: Path, channel_id: int) -> bool:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}", headers=headers
    )
    channel = body.get("data") if isinstance(body, dict) else None
    if status != 200 or not isinstance(channel, dict):
        return False
    if channel.get("status") != 2 or channel.get("weight") != 0:
        return False
    rows = read_ability_posture(db_path, channel_id)
    return bool(rows) and all(row[1] == 0 and row[3] == 0 for row in rows)


def set_channel_posture(
    smoke,
    headers: dict,
    channel: dict,
    db_path: Path,
    status: int,
    weight: int,
    *,
    force_weight: bool = False,
) -> None:
    channel_id = int(channel["id"])
    needs_weight = force_weight or channel.get("weight") != weight
    # Hydrate before the first mutation. A masked/reused key must never leave
    # the channel status changed while the subsequent PUT is guaranteed to fail.
    hydrated = hydrate_key(channel, db_path) if needs_weight else channel

    def put_weight() -> None:
        updated = {key: value for key, value in hydrated.items() if key != "status"}
        updated["weight"] = weight
        status_code, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/",
            method="PUT",
            body=updated,
            headers=headers,
        )
        if status_code != 200 or not isinstance(body, dict) or not body.get("success"):
            raise RuntimeError(
                f"channel {channel_id}: weight update failed HTTP {status_code}"
            )

    def set_status() -> None:
        status_code, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}/status",
            method="POST",
            body={"status": status},
            headers=headers,
        )
        if status_code != 200 or not isinstance(body, dict) or not body.get("success"):
            raise RuntimeError(
                f"channel {channel_id}: status update failed HTTP {status_code}"
            )

    if status == 1:
        # PUT rebuilds ability rows from the channel posture. Restore the
        # weight while disabled, then make the channel routable.
        if needs_weight:
            put_weight()
        set_status()
    else:
        set_status()
        if needs_weight:
            put_weight()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("channel_ids", nargs="+", type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if any(channel_id <= 0 for channel_id in args.channel_ids):
        parser.error("channel ids must be positive")

    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}
    channels: list[dict] = []
    for channel_id in args.channel_ids:
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}", headers=headers
        )
        channel = body.get("data") if isinstance(body, dict) else None
        if status != 200 or not isinstance(channel, dict):
            print(f"channel {channel_id}: read failed HTTP {status}")
            return 1
        channels.append(channel)
        print(
            f"channel {channel_id}: name={channel.get('name')} "
            f"status={channel.get('status')} weight={channel.get('weight')}"
        )

    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup_dir = Path(smoke.DEPLOY_DIR) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    joined = "-".join(str(channel_id) for channel_id in args.channel_ids)
    backup = backup_dir / f"channels-{joined}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    backup.write_text(
        json.dumps(
            {"channels": [safe_channel_summary(channel) for channel in channels]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"backup: {backup.name}")
    db_backup = online_backup(db_path, backup_dir, args.channel_ids)
    print(f"database backup: {db_backup.name} integrity=ok")

    try:
        for channel in channels:
            set_channel_posture(smoke, headers, channel, db_path, 2, 0)
            if not verify_quarantined(smoke, headers, db_path, int(channel["id"])):
                raise RuntimeError(f"channel {channel['id']}: quarantine readback failed")
            print(f"channel {channel['id']}: verified=true status=2 weight=0 abilities_weight=0")
    except Exception as error:
        print(f"apply failed; restoring original channel postures: {error}")
        for channel in channels:
            try:
                set_channel_posture(
                    smoke,
                    headers,
                    channel,
                    db_path,
                    int(channel.get("status", 2)),
                    int(channel.get("weight", 0)),
                    force_weight=True,
                )
            except Exception as rollback_error:
                print(f"rollback failed channel {channel.get('id')}: {rollback_error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
