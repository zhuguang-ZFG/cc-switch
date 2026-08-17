"""Enforce the Sol routing posture across both channels.

Promotes ch83 muyuan-sol to primary (priority 50 / weight 5) and demotes
ch45 agentrouter to fallback (priority 40 / weight 5), at both the channel
and ability level, so Sol traffic prefers muyuan and only falls back to
agentrouter when muyuan is disabled.

The promotion alone is not enough: agentrouter keeps its old primary priority
(51) until it is explicitly demoted, and NewAPI routes by the higher
model-level priority. This script applies both sides atomically so the
"muyuan primary" decision is actually in effect.

Run: python3 scripts/ops/update_muyuan_sol_primary.py [--apply]
"""
from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import time
from pathlib import Path


# (channel_id, expected_name, priority, weight, role)
TARGETS: tuple[tuple[int, str, int, int, str], ...] = (
    (83, "muyuan-sol", 50, 5, "primary"),
    (45, "agentrouter", 40, 5, "fallback"),
)
SOL_MODELS: tuple[str, ...] = (
    "gpt-5.6-sol",
    "zg-gpt-5.6-sol",
    "zg-agent-gpt-5.6-sol",
)


def load_smoke():
    path = Path(__file__).with_name("newapi-local-smoke.py")
    spec = importlib.util.spec_from_file_location(
        "newapi_local_smoke_for_updater", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_update(channel: dict, priority: int, weight: int) -> dict:
    """Return a PUT payload that sets priority/weight and preserves the key."""
    if channel.get("id") is None or channel.get("name") is None:
        raise RuntimeError("channel payload missing id or name")
    key = channel.get("key")
    if not isinstance(key, str) or not key or "*" in key:
        raise RuntimeError("channel key is empty or masked")
    updated = {name: value for name, value in channel.items() if name != "status"}
    updated["priority"] = priority
    updated["weight"] = weight
    return updated


def hydrate_channel_key(channel: dict, smoke, channel_id: int) -> dict:
    """Unmask a channel key from the local SSOT when the API redacts it."""
    key = channel.get("key")
    if isinstance(key, str) and key.strip() and "*" not in key:
        return channel
    con = sqlite3.connect(f"file:{Path(smoke.NEWAPI_DB).as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT key FROM channels WHERE id = ?", (channel_id,)
        ).fetchone()
    finally:
        con.close()
    actual = row[0] if row else None
    if not isinstance(actual, str) or not actual.strip():
        raise RuntimeError(f"channel {channel_id} key unavailable in local SSOT")
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


def read_ability_rows(db_path: Path, channel_id: int) -> list[tuple[str, int, int, int]]:
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as con:
        return list(
            con.execute(
                "SELECT model, enabled, priority, weight FROM abilities "
                "WHERE channel_id = ? ORDER BY model",
                (channel_id,),
            )
        )


def verify_abilities(
    rows: list[tuple[str, int, int, int]], priority: int, weight: int,
    *, enabled: int = 1,
) -> bool:
    """Confirm each Sol selector on the channel sits at the expected tier."""
    actual = {
        model: (enabled, prio, w)
        for model, enabled, prio, w in rows
        if model in SOL_MODELS
    }
    expected = {
        model: (enabled, priority, weight)
        for model in SOL_MODELS
    }
    return actual == expected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    channels: dict[int, dict] = {}
    for channel_id, _, _, _, _ in TARGETS:
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}", headers=headers
        )
        channel = body.get("data") if isinstance(body, dict) else None
        if status != 200 or not isinstance(channel, dict):
            print(f"channel {channel_id} read failed: HTTP {status}")
            return 1
        channels[channel_id] = channel

    updates: dict[int, dict] = {}
    originals: dict[int, dict] = {}
    for channel_id, expected_name, priority, weight, role in TARGETS:
        channel = channels[channel_id]
        if channel.get("name") != expected_name:
            print(
                f"channel {channel_id} expected name {expected_name!r}, "
                f"got {channel.get('name')!r}"
            )
            return 1
        channel = hydrate_channel_key(channel, smoke, channel_id)
        originals[channel_id] = channel
        updates[channel_id] = build_update(channel, priority, weight)
        print(
            f"ch{channel_id} {role}: priority {channel.get('priority')}→{priority}, "
            f"weight {channel.get('weight')}→{weight}"
        )

    already_ok = True
    for channel_id, _, priority, weight, _ in TARGETS:
        channel = channels[channel_id]
        rows = read_ability_rows(Path(smoke.NEWAPI_DB), channel_id)
        channel_ok = (
            channel.get("priority") == priority
            and channel.get("weight") == weight
        )
        expected_enabled = 1 if channel.get("status") == 1 else 0
        ability_ok = verify_abilities(
            rows, priority, weight, enabled=expected_enabled
        )
        if not (channel_ok and ability_ok):
            already_ok = False
        print(
            f"ch{channel_id} current channel_ok={channel_ok} ability_ok={ability_ok}"
        )

    if already_ok:
        print("already configured")
        return 0
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup_dir = Path(smoke.DEPLOY_DIR) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"new-api-before-sol-posture-{time.strftime('%Y%m%d-%H%M%S')}.db"
    backup_database(Path(smoke.NEWAPI_DB), backup)

    for channel_id in updates:
        put_status, put_body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/",
            method="PUT",
            body=updates[channel_id],
            headers=headers,
        )
        if put_status != 200 or not isinstance(put_body, dict) or not put_body.get("success"):
            print(f"update ch{channel_id} failed: HTTP {put_status}; backup={backup.name}")
            rollback(channels, originals, smoke, headers, channel_id)
            return 1

    verified = True
    for channel_id, _, priority, weight, _ in TARGETS:
        verify_status, verify_body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}", headers=headers
        )
        verified_channel = verify_body.get("data") if isinstance(verify_body, dict) else None
        rows = read_ability_rows(Path(smoke.NEWAPI_DB), channel_id)
        channel_ok = (
            verify_status == 200
            and isinstance(verified_channel, dict)
            and verified_channel.get("priority") == priority
            and verified_channel.get("weight") == weight
        )
        expected_enabled = (
            1
            if isinstance(verified_channel, dict)
            and verified_channel.get("status") == 1
            else 0
        )
        ability_ok = verify_abilities(
            rows, priority, weight, enabled=expected_enabled
        )
        print(f"ch{channel_id} verified channel_ok={channel_ok} ability_ok={ability_ok}")
        if not (channel_ok and ability_ok):
            verified = False

    if verified:
        print(f"backup={backup.name} verified=True")
        return 0

    print(f"verification failed; rolling back; backup={backup.name}")
    rollback_ok = rollback(channels, originals, smoke, headers, None)
    print(f"rollback_ok={rollback_ok}")
    return 1


def rollback(
    channels: dict[int, dict],
    originals: dict[int, dict],
    smoke,
    headers,
    skip_id: int | None,
) -> bool:
    """Restore every original channel payload (skipping one already restored)."""
    ok = True
    for channel_id, original in originals.items():
        if channel_id == skip_id:
            continue
        rb_status, rb_body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/",
            method="PUT",
            body=original,
            headers=headers,
        )
        ok = ok and (
            rb_status == 200
            and isinstance(rb_body, dict)
            and bool(rb_body.get("success"))
        )
    return ok


if __name__ == "__main__":
    raise SystemExit(main())
