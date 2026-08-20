#!/usr/bin/env python3
"""Aggregate the two justwoker (https://api.justwoker.icu) keys into local
NewAPI as two single-key OpenAI channels.

The upstream serves only claude-opus-5-thinking and claude-opus-4-8-thinking
(verified 2026-08-20 via /v1/models for both keys). Two separate single-key
channels are used instead of one multi-key channel because this fork has
documented multi-key pitfalls (see the t1qq runbook,
docs/ops/t1qq-sol-channel-2026-08-16.md).

The upstream sits behind Cloudflare and returns error 1010 for non-browser
User-Agents (python-urllib and curl verified blocked), so both channels carry
a header_override with a browser UA. The disabled-channel management probe
proves the full NewAPI -> upstream path before either channel is enabled.

Posture: priority 50 / weight 8, peer with tabitoken ch75 in the thinking
pool (posture history is in the runbook, docs/ops/justwoker-opus-channels-2026-08-20.md).

Workflow contract (same as ch83-ch92 scripts):
- dup check by name and (base_url, models) before creating
- whole-DB SQLite snapshot backup before any change
- POST /api/channel/ with {"mode":"single","channel":payload} double wrap
- create disabled (status=2), management probe while disabled, enable only
  after the probe passes
- channel + abilities readback verification after apply

Run without --apply for a read-only plan. Keys come from argv and are never
printed (masked to first/last 4 chars). Re-running with channels already
present only probes and verifies them — it never creates duplicates, never
changes an existing channel's status (an intentional or Guardian-driven
disable is not silently re-enabled), and never updates an existing channel's
key (a rotated key via argv does NOT take effect on an existing channel;
delete or edit the channel first).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")

BASE_URL = "https://api.justwoker.icu"  # NewAPI appends /v1/chat/completions
MODELS = "claude-opus-5-thinking,claude-opus-4-8-thinking"
TEST_MODEL = "claude-opus-4-8-thinking"
PRIORITY = 50
WEIGHT = 8
CACHE_SYNC_SECONDS = 75
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HEADER_OVERRIDE = json.dumps({"User-Agent": BROWSER_UA})
CHANNELS = (
    ("justwoker-opus-1", "key1"),
    ("justwoker-opus-2", "key2"),
)


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mask(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]} (len={len(key)})"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key1", help="first justwoker API key (never printed)")
    parser.add_argument("key2", help="second justwoker API key (never printed)")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the backed-up live change; default is read-only",
    )
    return parser.parse_args()


def list_channels(smoke, headers: dict[str, str]) -> list[dict]:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=200", headers=headers
    )
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"channel list failed: HTTP {status}")
    items = body.get("data") or []
    if isinstance(items, dict):
        items = items.get("items") or []
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise RuntimeError("channel list has invalid shape")
    if len(items) >= 200:
        # Single-page fetch: planned ids and readbacks silently corrupt past
        # the page boundary. Fail fast instead of paginating (one-shot tool).
        raise RuntimeError("channel list page full (>=200); paginate before use")
    return items


def channel_payload(name: str, key: str) -> dict:
    return {
        "name": name,
        "type": 1,  # OpenAI (/v1/chat/completions)
        "key": key,
        "base_url": BASE_URL,
        "models": MODELS,
        "group": "default",
        "header_override": HEADER_OVERRIDE,
        "test_model": TEST_MODEL,
        "priority": PRIORITY,
        "weight": WEIGHT,
        "status": 2,  # created disabled; enabled only after probe passes
        "auto_ban": 1,
    }


def set_status(smoke, headers: dict[str, str], channel_id: int, status: int) -> None:
    response_status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}/status",
        method="POST",
        body={"status": status},
        headers=headers,
    )
    if response_status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(
            f"channel {channel_id} status={status} failed: HTTP {response_status}"
        )


def management_probe(smoke, headers: dict[str, str], channel_id: int) -> None:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={TEST_MODEL}",
        headers=headers,
        timeout=65,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(
            f"management probe failed: HTTP {status} message={message!r}"
        )


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        f"new-api-before-justwoker-opus-{time.strftime('%Y%m%d-%H%M%S')}.db"
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


def verify(
    db_path: Path, items: list[dict], name: str, channel_id: int,
    expected_status: int = 1,
    strict: bool = True,
) -> None:
    """Readback-check one channel.

    strict=True (channels created by this run) also pins priority/weight and
    auto_ban. strict=False (pre-existing channels) checks identity fields only:
    Guardian's weight closed loop legitimately drifts posture on non-fixed
    routes, and a re-run must not fail on that drift.
    """
    channel = next((i for i in items if i.get("id") == channel_id), None)
    if channel is None:
        raise RuntimeError(f"ch{channel_id} missing on readback")
    expected = {
        "name": name,
        "type": 1,
        "status": expected_status,
        "base_url": BASE_URL,
        "models": MODELS,
        "test_model": TEST_MODEL,
    }
    if strict:
        expected.update({"auto_ban": 1, "priority": PRIORITY, "weight": WEIGHT})
    mismatch = {
        field: (channel.get(field), value)
        for field, value in expected.items()
        if channel.get(field) != value
    }
    try:
        header_ok = json.loads(str(channel.get("header_override") or "null")) == {
            "User-Agent": BROWSER_UA
        }
    except json.JSONDecodeError:
        header_ok = False
    if not header_ok:
        mismatch["header_override"] = ("drifted", "expected")

    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        rows = connection.execute(
            "SELECT model, enabled, priority, weight FROM abilities "
            "WHERE channel_id = ? ORDER BY model",
            (channel_id,),
        ).fetchall()
    abilities = {model: (enabled, priority, weight) for model, enabled, priority, weight in rows}
    ability_enabled = 1 if expected_status == 1 else 0
    if strict:
        abilities_ok = abilities == {
            model: (ability_enabled, PRIORITY, WEIGHT) for model in MODELS.split(",")
        }
    else:
        abilities_ok = set(abilities) == set(MODELS.split(",")) and all(
            enabled == ability_enabled for enabled, _, _ in abilities.values()
        )
    if mismatch or not abilities_ok:
        raise RuntimeError(
            f"readback mismatch for ch{channel_id}: "
            f"channel={mismatch or 'ok'} abilities_ok={abilities_ok}"
        )


def main() -> int:
    args = parse_args()
    keys = {"key1": args.key1.strip(), "key2": args.key2.strip()}
    for label, key in keys.items():
        if not key:
            raise RuntimeError(f"{label} must not be empty")
    if keys["key1"] == keys["key2"]:
        raise RuntimeError("key1 and key2 are identical; refusing duplicate channel")

    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    token, user_id = smoke.admin_auth()
    headers = {
        "Authorization": f"Bearer {token}",
        "New-Api-User": str(user_id),
    }
    items = list_channels(smoke, headers)
    existing: dict[str, dict] = {}
    sibling_names = {name for name, _ in CHANNELS}
    for name, _ in CHANNELS:
        named = [i for i in items if i.get("name") == name]
        named_ids = {int(i["id"]) for i in named if isinstance(i.get("id"), int)}
        if len(named_ids) > 1:
            raise RuntimeError(f"duplicate channel name {name!r}: {sorted(named_ids)}")
        if named_ids:
            # Name wins. The sibling channel shares base_url+models by design,
            # so the equivalent check only applies when no named match exists.
            existing[name] = next(i for i in named if isinstance(i.get("id"), int))
            continue
        # Exclude siblings and already-claimed ids: with one channel deleted,
        # the survivor must not be "adopted" as the missing sibling.
        claimed = {int(v["id"]) for v in existing.values()}
        equivalent = [
            i
            for i in items
            if i.get("base_url") == BASE_URL
            and str(i.get("models") or "") == MODELS
            and i.get("name") not in sibling_names
            and (not isinstance(i.get("id"), int) or int(i["id"]) not in claimed)
        ]
        eq_ids = {int(i["id"]) for i in equivalent if isinstance(i.get("id"), int)}
        if len(eq_ids) > 1:
            raise RuntimeError(
                f"ambiguous equivalent channels for {name!r}: {sorted(eq_ids)}"
            )
        if eq_ids:
            existing[name] = next(i for i in equivalent if isinstance(i.get("id"), int))
    max_id = max(
        (int(i["id"]) for i in items if isinstance(i.get("id"), int)), default=0
    )
    todo = [name for name, _ in CHANNELS if name not in existing]
    planned_ids = {name: max_id + n + 1 for n, name in enumerate(todo)}
    for name, key_label in CHANNELS:
        if name in existing:
            print(f"plan: {name} exists as ch{existing[name]['id']}; verify only")
        else:
            print(
                f"plan: create {name} as ch{planned_ids[name]} "
                f"({key_label}={mask(keys[key_label])}) disabled, probe, enable "
                f"at p{PRIORITY}/w{WEIGHT}"
            )
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = None
    if todo:
        backup = online_backup(db_path)
        print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    created_ids: dict[str, int] = {}
    created_new: set[str] = set()
    try:
        for name, key_label in CHANNELS:
            if name in existing:
                channel_id = int(existing[name]["id"])
                created_ids[name] = channel_id
                # Never touch an existing channel's status: an intentional or
                # Guardian-driven disable must not be silently re-enabled by
                # re-running this script. Probe and verify only.
                management_probe(smoke, headers, channel_id)
                print(f"ch{channel_id} {name} exists (status={existing[name].get('status')}); probe ok, status untouched")
                continue
            status, body = smoke.http_json(
                f"{smoke.NEWAPI_BASE}/api/channel/",
                method="POST",
                body={"mode": "single", "channel": channel_payload(name, keys[key_label])},
                headers=headers,
            )
            if status != 200 or not isinstance(body, dict) or not body.get("success"):
                message = body.get("message") if isinstance(body, dict) else None
                raise RuntimeError(f"create {name} failed: HTTP {status} message={message!r}")
            items = list_channels(smoke, headers)
            created = next((i for i in items if i.get("name") == name), None)
            if created is None or not isinstance(created.get("id"), int):
                raise RuntimeError(f"created {name} missing on readback")
            channel_id = int(created["id"])
            if channel_id != planned_ids[name]:
                set_status(smoke, headers, channel_id, 2)
                raise RuntimeError(
                    f"created unexpected channel id {channel_id} for {name}; "
                    f"expected {planned_ids[name]} (left disabled; manual "
                    f"enable or delete + re-run required)"
                )
            if created.get("status") != 2:
                # Double-lock: do not trust the create payload's status=2 to
                # be honored by the fork (same belt-and-suspenders as ch92).
                set_status(smoke, headers, channel_id, 2)
            created_ids[name] = channel_id
            created_new.add(name)
            print(f"ch{channel_id} {name} created disabled")

            management_probe(smoke, headers, channel_id)
            print(f"ch{channel_id} {name} management probe ok ({TEST_MODEL})")
            set_status(smoke, headers, channel_id, 1)
            print(f"ch{channel_id} {name} enabled at p{PRIORITY}/w{WEIGHT}")

        if created_new:
            print(f"waiting {CACHE_SYNC_SECONDS}s for channel cache sync")
            time.sleep(CACHE_SYNC_SECONDS)
            items = list_channels(smoke, headers)
        for name, channel_id in created_ids.items():
            strict = name in created_new
            expected_status = 1 if strict else int(existing[name]["status"])
            verify(db_path, items, name, channel_id, expected_status, strict=strict)
        parts = []
        for name, cid in created_ids.items():
            if name in created_new:
                parts.append(f"ch{cid} {name} live at p{PRIORITY}/w{WEIGHT}")
            else:
                parts.append(
                    f"ch{cid} {name} present, status={existing[name].get('status')} untouched"
                )
        print(f"OK: {', '.join(parts)}; backup={backup.name if backup else '(not needed)'}")
        return 0
    except Exception:
        # Roll back only what this run created; pre-existing channels keep
        # their status untouched.
        for name in created_new:
            channel_id = created_ids[name]
            try:
                set_status(smoke, headers, channel_id, 2)
                print(f"rollback: ch{channel_id} {name} disabled")
            except Exception as error:
                print(f"rollback warning: could not disable ch{channel_id}: {error}")
        if backup is not None:
            print(f"rollback attempted; full snapshot={backup.name}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
