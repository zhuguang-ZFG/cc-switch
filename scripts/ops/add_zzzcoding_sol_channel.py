#!/usr/bin/env python3
"""Add zzzcoding as the NewAPI Sol primary and demote jianzhile.

The zzzcoding upstream lists gpt-5.6-sol but rejects generic Chat
Completions. It accepts the Codex Responses wire shape, so this tool creates
an isolated type-1 channel, enables NewAPI's channel-local Chat-to-Responses
conversion, verifies the streaming Responses probe while the channel is
disabled, and only then enables it.

The user-selected routing posture is:

  ch92 zzzcoding  priority 60 / weight 15  primary
  ch91 jianzhile  priority 50 / weight 5   backup

Run without --apply for a read-only plan. The API key is never printed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from fix_jianzhile_codex_channel import (
    CHAT_RESPONSES_OPTION,
    HEADER_OVERRIDE,
    PARAM_OVERRIDE,
    merge_chat_responses_policy,
)


SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
CHANNEL_ID = 92
CHANNEL_NAME = "zzzcoding-gpt-5.6-sol"
BASE_URL = "https://api.zzzcoding.org"
CODEX_MODEL = "zzzcoding-codex-gpt-5.6-sol"
UPSTREAM_MODEL = "gpt-5.6-sol"
MODELS = (
    "gpt-5.6-sol",
    "zg-gpt-5.6-sol",
    "zg-agent-gpt-5.6-sol",
    CODEX_MODEL,
)
MODEL_MAPPING = {
    "zg-gpt-5.6-sol": UPSTREAM_MODEL,
    "zg-agent-gpt-5.6-sol": UPSTREAM_MODEL,
    CODEX_MODEL: UPSTREAM_MODEL,
}
MODEL_PATTERNS = [
    r"^gpt-5\.6-sol$",
    r"^zg-gpt-5\.6-sol$",
    r"^zg-agent-gpt-5\.6-sol$",
    r"^zzzcoding-codex-gpt-5\.6-sol$",
]
PEER_ID = 91
PEER_NAME = "jianzhile-gpt-5.6-sol"
PEER_PRIORITY = 50
PEER_WEIGHT = 5
PRIORITY = 60
WEIGHT = 15
CACHE_SYNC_SECONDS = 75
ZZZCODING_PARAM_OVERRIDE = {
    **PARAM_OVERRIDE,
    "parallel_tool_calls": False,
}


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key", help="zzzcoding API key (never printed)")
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
        "new-api-before-zzzcoding-sol-primary-"
        f"{time.strftime('%Y%m%d-%H%M%S')}.db"
    )
    if destination.exists():
        raise RuntimeError(f"backup already exists: {destination}")
    source = sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )
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


def list_channels(smoke, headers: dict[str, str]) -> list[dict]:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=200", headers=headers
    )
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"channel list failed: HTTP {status}")
    items = body.get("data") or []
    if isinstance(items, dict):
        items = items.get("items") or []
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise RuntimeError("channel list has invalid shape")
    return items


def find_channel(items: list[dict]) -> dict | None:
    named = [item for item in items if item.get("name") == CHANNEL_NAME]
    equivalent = [
        item
        for item in items
        if item.get("base_url") == BASE_URL
        and CODEX_MODEL in str(item.get("models") or "").split(",")
    ]
    collisions = {
        int(item["id"])
        for item in [*named, *equivalent]
        if isinstance(item.get("id"), int)
    }
    if len(collisions) > 1:
        raise RuntimeError(f"multiple zzzcoding channel collisions: {sorted(collisions)}")
    return (named or equivalent or [None])[0]


def channel_payload(key: str) -> dict:
    return {
        "name": CHANNEL_NAME,
        "type": 1,
        "key": key,
        "base_url": BASE_URL,
        "models": ",".join(MODELS),
        "group": "default",
        "model_mapping": json.dumps(
            MODEL_MAPPING, separators=(",", ":"), sort_keys=True
        ),
        "header_override": json.dumps(
            HEADER_OVERRIDE, separators=(",", ":"), sort_keys=True
        ),
        "param_override": json.dumps(
            ZZZCODING_PARAM_OVERRIDE, separators=(",", ":"), sort_keys=True
        ),
        "test_model": CODEX_MODEL,
        "priority": PRIORITY,
        "weight": WEIGHT,
        "status": 2,
        "auto_ban": 1,
    }


def set_status(smoke, headers: dict[str, str], channel_id: int, status: int) -> None:
    response_status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}/status",
        method="POST",
        body={"status": status},
        headers=headers,
    )
    if (
        response_status != 200
        or not isinstance(body, dict)
        or not body.get("success")
    ):
        raise RuntimeError(
            f"channel {channel_id} status={status} failed: HTTP {response_status}"
        )


def hydrate_channel_key(db_path: Path, channel: dict) -> dict:
    key = channel.get("key")
    if isinstance(key, str) and key.strip() and "*" not in key:
        return channel
    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        row = connection.execute(
            "SELECT key FROM channels WHERE id = ?", (channel.get("id"),)
        ).fetchone()
    actual = row[0] if row else None
    if not isinstance(actual, str) or not actual.strip():
        raise RuntimeError(f"channel {channel.get('id')} key unavailable")
    return {**channel, "key": actual}


def desired_existing_channel(channel: dict) -> dict:
    updated = {field: value for field, value in channel.items() if field != "status"}
    desired = channel_payload(str(channel["key"]))
    desired.pop("status")
    updated.update(desired)
    updated["id"] = CHANNEL_ID
    return updated


def put_channel(smoke, headers: dict[str, str], channel: dict) -> None:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/",
        method="PUT",
        body=channel,
        headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"channel update failed: HTTP {status}")


def put_policy(smoke, headers: dict[str, str], value: str) -> None:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/option/",
        method="PUT",
        body={"key": CHAT_RESPONSES_OPTION, "value": value},
        headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"Responses policy update failed: HTTP {status}")


def update_peer_posture(
    connection: sqlite3.Connection, priority: int, weight: int
) -> tuple[int, int]:
    row = connection.execute(
        "SELECT name, priority, weight FROM channels WHERE id = ?", (PEER_ID,)
    ).fetchone()
    if row is None or row[0] != PEER_NAME:
        raise RuntimeError(f"ch{PEER_ID} is not {PEER_NAME!r}")
    original = (int(row[1]), int(row[2]))
    with connection:
        connection.execute(
            "UPDATE channels SET priority = ?, weight = ? WHERE id = ?",
            (priority, weight, PEER_ID),
        )
        connection.execute(
            "UPDATE abilities SET priority = ?, weight = ? WHERE channel_id = ?",
            (priority, weight, PEER_ID),
        )
    return original


def management_probe(smoke, headers: dict[str, str], channel_id: int) -> None:
    path = (
        f"/api/channel/test/{channel_id}?model={CODEX_MODEL}"
        "&endpoint_type=openai-response&stream=true"
    )
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}{path}", headers=headers, timeout=65
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(
            f"streaming Responses probe failed: HTTP {status} message={message!r}"
        )


def verify(
    db_path: Path, items: list[dict], channel_id: int
) -> None:
    channel = next((item for item in items if item.get("id") == channel_id), None)
    if channel is None:
        raise RuntimeError(f"ch{channel_id} missing on readback")
    expected = {
        "name": CHANNEL_NAME,
        "type": 1,
        "status": 1,
        "auto_ban": 1,
        "base_url": BASE_URL,
        "models": ",".join(MODELS),
        "test_model": CODEX_MODEL,
        "priority": PRIORITY,
        "weight": WEIGHT,
    }
    mismatch = {
        field: (channel.get(field), value)
        for field, value in expected.items()
        if channel.get(field) != value
    }
    for field, expected_json in (
        ("model_mapping", MODEL_MAPPING),
        ("header_override", HEADER_OVERRIDE),
        ("param_override", ZZZCODING_PARAM_OVERRIDE),
    ):
        try:
            actual = json.loads(str(channel.get(field) or "null"))
        except json.JSONDecodeError:
            actual = None
        if actual != expected_json:
            mismatch[field] = ("drifted", "expected")

    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        peer = connection.execute(
            "SELECT priority, weight FROM channels WHERE id = ?", (PEER_ID,)
        ).fetchone()
        rows = connection.execute(
            "SELECT model, enabled, priority, weight FROM abilities "
            "WHERE channel_id = ? ORDER BY model",
            (channel_id,),
        ).fetchall()
    abilities = {
        model: (enabled, priority, weight)
        for model, enabled, priority, weight in rows
    }
    abilities_ok = abilities == {
        model: (1, PRIORITY, WEIGHT) for model in MODELS
    }
    if mismatch or peer != (PEER_PRIORITY, PEER_WEIGHT) or not abilities_ok:
        raise RuntimeError(
            "readback mismatch: "
            f"channel={mismatch or 'ok'} peer={peer} abilities_ok={abilities_ok}"
        )


def main() -> int:
    args = parse_args()
    if not args.key.strip():
        raise RuntimeError("API key must not be empty")

    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    token, user_id = smoke.admin_auth()
    headers = {
        "Authorization": f"Bearer {token}",
        "New-Api-User": str(user_id),
    }
    items = list_channels(smoke, headers)
    existing = find_channel(items)
    max_id = max(
        (int(item["id"]) for item in items if isinstance(item.get("id"), int)),
        default=0,
    )
    if existing is None and max_id != CHANNEL_ID - 1:
        raise RuntimeError(
            f"expected next channel id {CHANNEL_ID}, current max id is {max_id}"
        )

    with closing(sqlite3.connect(db_path, timeout=30)) as connection:
        option_row = connection.execute(
            "SELECT value FROM options WHERE key = ?", (CHAT_RESPONSES_OPTION,)
        ).fetchone()
        if option_row is None or not isinstance(option_row[0], str):
            raise RuntimeError(f"{CHAT_RESPONSES_OPTION} is missing")
        original_policy = option_row[0]
        peer_row = connection.execute(
            "SELECT name, priority, weight FROM channels WHERE id = ?", (PEER_ID,)
        ).fetchone()
    if peer_row is None or peer_row[0] != PEER_NAME:
        raise RuntimeError(f"ch{PEER_ID} is not {PEER_NAME!r}")

    print(
        f"plan: ch{PEER_ID} {peer_row[1]}/{peer_row[2]} -> "
        f"{PEER_PRIORITY}/{PEER_WEIGHT}; "
        f"ch{CHANNEL_ID} {'verify' if existing else 'create disabled, probe, enable'} "
        f"at {PRIORITY}/{WEIGHT}"
    )
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")
    original_peer = (int(peer_row[1]), int(peer_row[2]))
    channel_id: int | None = int(existing["id"]) if existing else None
    original_channel = (
        hydrate_channel_key(db_path, existing) if existing is not None else None
    )
    channel_changed = False
    policy_changed = False
    try:
        if existing is None:
            status, body = smoke.http_json(
                f"{smoke.NEWAPI_BASE}/api/channel/",
                method="POST",
                body={"mode": "single", "channel": channel_payload(args.key.strip())},
                headers=headers,
            )
            if status != 200 or not isinstance(body, dict) or not body.get("success"):
                raise RuntimeError(f"channel create failed: HTTP {status}")
            items = list_channels(smoke, headers)
            created = find_channel(items)
            if created is None or not isinstance(created.get("id"), int):
                raise RuntimeError("created channel missing on readback")
            channel_id = int(created["id"])
            if channel_id != CHANNEL_ID:
                set_status(smoke, headers, channel_id, 2)
                raise RuntimeError(
                    f"created unexpected channel id {channel_id}; expected {CHANNEL_ID}"
                )
            set_status(smoke, headers, channel_id, 2)
            print(f"ch{channel_id} created disabled")
        else:
            put_channel(
                smoke,
                headers,
                desired_existing_channel(original_channel),
            )
            channel_changed = True
            set_status(smoke, headers, channel_id, 2)
            print(f"ch{channel_id} existing configuration refreshed, still disabled")

        if channel_id != CHANNEL_ID:
            raise RuntimeError(f"existing zzzcoding channel id is {channel_id}, expected {CHANNEL_ID}")
        desired_policy = merge_chat_responses_policy(
            original_policy, channel_id, MODEL_PATTERNS
        )
        put_policy(smoke, headers, desired_policy)
        policy_changed = True
        management_probe(smoke, headers, channel_id)
        print(f"ch{channel_id} streaming Responses probe ok")
        with closing(sqlite3.connect(db_path, timeout=30)) as connection:
            update_peer_posture(connection, PEER_PRIORITY, PEER_WEIGHT)
        set_status(smoke, headers, channel_id, 1)
        print("primary/backup posture written; waiting for channel cache")
        time.sleep(CACHE_SYNC_SECONDS)
        items = list_channels(smoke, headers)
        verify(db_path, items, channel_id)
        print(
            f"OK: ch{channel_id} primary {PRIORITY}/{WEIGHT}; "
            f"ch{PEER_ID} backup {PEER_PRIORITY}/{PEER_WEIGHT}; backup={backup.name}"
        )
        return 0
    except Exception:
        if channel_id is not None:
            try:
                set_status(smoke, headers, channel_id, 2)
            except Exception as error:
                print(f"rollback warning: could not disable ch{channel_id}: {error}")
        if policy_changed:
            try:
                put_policy(smoke, headers, original_policy)
            except Exception as error:
                print(f"rollback warning: could not restore Responses policy: {error}")
        if channel_changed and original_channel is not None:
            try:
                put_channel(
                    smoke,
                    headers,
                    {
                        field: value
                        for field, value in original_channel.items()
                        if field != "status"
                    },
                )
                set_status(smoke, headers, CHANNEL_ID, 2)
            except Exception as error:
                print(f"rollback warning: could not restore ch{CHANNEL_ID}: {error}")
        try:
            with closing(sqlite3.connect(db_path, timeout=30)) as connection:
                update_peer_posture(connection, *original_peer)
        except Exception as error:
            print(f"rollback warning: could not restore ch{PEER_ID}: {error}")
        print(f"rollback attempted; full snapshot={backup.name}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
