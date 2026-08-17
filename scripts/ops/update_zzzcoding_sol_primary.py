#!/usr/bin/env python3
"""Enforce zzzcoding primary and jianzhile backup for the Sol pool.

The default mode is read-only. ``--apply`` creates an integrity-checked online
SQLite snapshot, updates only channel/ability priority and weight, waits for
NewAPI's channel cache, and probes ch92 through streaming Responses. Any
failure restores both channel tiers and their ability rows.
"""

from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import time
from contextlib import closing
from pathlib import Path


SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
CACHE_SYNC_SECONDS = 75
TARGETS: tuple[tuple[int, str, int, int, str], ...] = (
    (92, "zzzcoding-gpt-5.6-sol", 60, 15, "primary"),
    (91, "jianzhile-gpt-5.6-sol", 50, 5, "backup"),
)
EXPECTED_MODELS: dict[int, frozenset[str]] = {
    92: frozenset(
        {
            "gpt-5.6-sol",
            "zg-gpt-5.6-sol",
            "zg-agent-gpt-5.6-sol",
            "zzzcoding-codex-gpt-5.6-sol",
        }
    ),
    91: frozenset(
        {
            "gpt-5.6-sol",
            "zg-gpt-5.6-sol",
            "zg-agent-gpt-5.6-sol",
            "jianzhile-codex-gpt-5.6-sol",
        }
    ),
}


def load_smoke():
    spec = importlib.util.spec_from_file_location(
        "newapi_local_smoke_for_zzzcoding_posture", SMOKE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the backed-up live change; default is read-only",
    )
    return parser.parse_args()


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        "new-api-before-zzzcoding-sol-posture-"
        f"{time.strftime('%Y%m%d-%H%M%S')}.db"
    )
    if destination.exists():
        raise RuntimeError(f"backup already exists: {destination}")
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as source, closing(sqlite3.connect(destination, timeout=30)) as target:
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()
        if result != ("ok",):
            raise RuntimeError(f"backup integrity check failed: {result}")
    return destination


def read_state(connection: sqlite3.Connection) -> dict:
    state: dict = {"channels": {}, "abilities": {}}
    for channel_id, expected_name, _, _, _ in TARGETS:
        channel = connection.execute(
            "SELECT name, status, priority, weight, typeof(channel_info) "
            "FROM channels WHERE id = ?",
            (channel_id,),
        ).fetchone()
        if channel is None or channel[0] != expected_name:
            actual = channel[0] if channel else None
            raise RuntimeError(
                f"ch{channel_id} expected {expected_name!r}, got {actual!r}"
            )
        if channel[4] != "blob":
            raise RuntimeError(f"ch{channel_id} channel_info must remain a BLOB")
        state["channels"][channel_id] = tuple(channel)

        rows = list(
            connection.execute(
                "SELECT `group`, model, enabled, priority, weight "
                "FROM abilities WHERE channel_id = ? ORDER BY `group`, model",
                (channel_id,),
            )
        )
        models = frozenset(row[1] for row in rows)
        if models != EXPECTED_MODELS[channel_id] or len(rows) != len(models):
            raise RuntimeError(
                f"ch{channel_id} ability model set drift: {sorted(models)}"
            )
        state["abilities"][channel_id] = [tuple(row) for row in rows]
    return state


def write_targets(connection: sqlite3.Connection) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        for channel_id, _, priority, weight, _ in TARGETS:
            channel_result = connection.execute(
                "UPDATE channels SET priority = ?, weight = ? WHERE id = ?",
                (priority, weight, channel_id),
            )
            ability_result = connection.execute(
                "UPDATE abilities SET priority = ?, weight = ? WHERE channel_id = ?",
                (priority, weight, channel_id),
            )
            if channel_result.rowcount != 1:
                raise RuntimeError(f"ch{channel_id} update touched no channel row")
            if ability_result.rowcount != len(EXPECTED_MODELS[channel_id]):
                raise RuntimeError(
                    f"ch{channel_id} update touched {ability_result.rowcount} ability rows"
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def restore_state(connection: sqlite3.Connection, state: dict) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        for channel_id, channel in state["channels"].items():
            connection.execute(
                "UPDATE channels SET priority = ?, weight = ? WHERE id = ?",
                (channel[2], channel[3], channel_id),
            )
        for channel_id, rows in state["abilities"].items():
            for group, model, _enabled, priority, weight in rows:
                connection.execute(
                    "UPDATE abilities SET priority = ?, weight = ? "
                    "WHERE channel_id = ? AND `group` = ? AND model = ?",
                    (priority, weight, channel_id, group, model),
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def verify_targets(connection: sqlite3.Connection) -> None:
    state = read_state(connection)
    for channel_id, _, priority, weight, _ in TARGETS:
        channel = state["channels"][channel_id]
        if channel[2:4] != (priority, weight):
            raise RuntimeError(
                f"ch{channel_id} channel posture is {channel[2]}/{channel[3]}"
            )
        if any(row[3:5] != (priority, weight) for row in state["abilities"][channel_id]):
            raise RuntimeError(f"ch{channel_id} ability posture mismatch")


def management_probe(smoke, headers: dict[str, str]) -> None:
    path = (
        "/api/channel/test/92?model=zzzcoding-codex-gpt-5.6-sol"
        "&endpoint_type=openai-response&stream=true"
    )
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}{path}", headers=headers, timeout=65
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(
            f"ch92 streaming Responses probe failed: HTTP {status} message={message!r}"
        )


def main() -> int:
    args = parse_args()
    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()

    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        original = read_state(connection)

    for channel_id, name, priority, weight, role in TARGETS:
        current = original["channels"][channel_id]
        print(
            f"ch{channel_id} {name}: {current[2]}/{current[3]} -> "
            f"{priority}/{weight} ({role}, status preserved={current[1]})"
        )
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    token, user_id = smoke.admin_auth()
    headers = {
        "Authorization": f"Bearer {token}",
        "New-Api-User": str(user_id),
    }
    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    changed = False
    try:
        with closing(sqlite3.connect(db_path, timeout=30)) as connection:
            write_targets(connection)
            changed = True
            verify_targets(connection)
            if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise RuntimeError("live database integrity check failed")

        print(f"posture written; waiting {CACHE_SYNC_SECONDS}s for channel cache")
        time.sleep(CACHE_SYNC_SECONDS)
        management_probe(smoke, headers)
        with closing(
            sqlite3.connect(
                f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
            )
        ) as connection:
            verify_targets(connection)
        print(
            "OK: ch92 zzzcoding primary 60/15; "
            f"ch91 jianzhile backup 50/5; backup={backup.name}"
        )
        return 0
    except Exception:
        if changed:
            try:
                with closing(sqlite3.connect(db_path, timeout=30)) as connection:
                    restore_state(connection, original)
                    restored = read_state(connection)
                if restored != original:
                    raise RuntimeError("rollback readback differs from original state")
                print(
                    f"rollback restored both tiers; waiting {CACHE_SYNC_SECONDS}s "
                    "for channel cache"
                )
                time.sleep(CACHE_SYNC_SECONDS)
            except Exception as rollback_error:
                print(f"rollback warning: {rollback_error}; snapshot={backup.name}")
        print(f"apply failed; snapshot={backup.name}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
