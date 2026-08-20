#!/usr/bin/env python3
"""Split the tabitoken multi-key channel (ch75) into per-key single channels.

Root cause (2026-08-20): ch75 `tabitoken` is a 3-key polling multi-key
channel. Key #2 ran out of balance ($0.219 < $0.8 pre-charge) and its 403
tripped auto_ban, disabling the WHOLE channel even though keys #1 and #3 are
healthy — the fork's multi-key machinery does not skip a quota-dead key, it
kills the channel (the same class of pitfall documented for t1qq ch90 in
docs/ops/t1qq-sol-channel-2026-08-16.md).

Fix: three single-key type-14 channels `tabitoken-1/2/3`, one per key, copied
from ch75's configuration (type, base_url, models, model_mapping, test_model,
posture). A quota-dead key now only disables its own channel.

Per-key solvency is decided by a DIRECT upstream probe with a realistic
max_tokens (8192): the management test uses a tiny request whose pre-charge
passes even on a near-empty balance ($0.72 passed max_tokens=1 while failing
real $0.8-pre-charge traffic). Only solvent channels are enabled; an insolvent
key's channel is created disabled and LEFT disabled (top up the key, then
enable manually).

Keys are read from the ch75 DB row — never from argv, never printed (masked
to first/last 4 chars). ch75 itself is kept as a disabled tombstone; Guardian
must not resurrect it (polling would re-hit the dead key and re-trip
auto_ban), so add 75 to AUTO_BAN_RECOVERY_EXCLUSIONS in guardian.py and to
KNOWN_BROKEN_CHANNELS in newapi-local-smoke.py when applying this split.

Workflow contract (same as add_justwoker_opus_channel.py):
- dup check by name before creating
- whole-DB SQLite snapshot backup before any change
- POST /api/channel/ with {"mode":"single","channel":payload} double wrap
- create disabled (status=2), solvency probe + management probe, enable only
  solvent channels after both pass
- channel + abilities readback verification after apply

Run without --apply for a read-only plan. Re-running with channels already
present only probes and verifies them — it never creates duplicates, never
changes an existing channel's status, and never updates an existing channel's
key.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import time
import urllib.request
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")

SOURCE_CHANNEL_ID = 75
CHANNEL_NAMES = ("tabitoken-1", "tabitoken-2", "tabitoken-3")
TYPE = 14  # Claude relay, same as ch75
BASE_URL = "https://tabitoken.com"
MODELS = "claude-opus-5,claude-opus-5-thinking,claude-opus-4-8,claude-opus-4-8-thinking,zg-claude-opus-5"
MODEL_MAPPING = json.dumps({"zg-claude-opus-5": "claude-opus-5"})
TEST_MODEL = "claude-opus-5"
# Solvency probe: cheap model but realistic max_tokens so the upstream
# pre-charge (~$0.8) matches real traffic and exposes a low-balance key.
SOLVENCY_MODEL = "claude-opus-5"
SOLVENCY_MAX_TOKENS = 8192
PRIORITY = 50
WEIGHT = 8
CACHE_SYNC_SECONDS = 75
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
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
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the backed-up live change; default is read-only",
    )
    return parser.parse_args()


def read_source_keys(db_path: Path) -> list[str]:
    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        row = connection.execute(
            "SELECT key FROM channels WHERE id = ?", (SOURCE_CHANNEL_ID,)
        ).fetchone()
    if row is None or not isinstance(row[0], str):
        raise RuntimeError(f"ch{SOURCE_CHANNEL_ID} key blob missing")
    keys = [k.strip() for k in row[0].split("\n") if k.strip()]
    if len(keys) != len(CHANNEL_NAMES):
        raise RuntimeError(
            f"ch{SOURCE_CHANNEL_ID} has {len(keys)} keys, expected {len(CHANNEL_NAMES)}"
        )
    return keys


def solvency_probe(key: str) -> bool:
    """Direct upstream probe with realistic pre-charge. True = solvent."""
    payload = json.dumps({
        "model": SOLVENCY_MODEL,
        "max_tokens": SOLVENCY_MAX_TOKENS,
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
    }).encode()
    request = urllib.request.Request(
        f"{BASE_URL}/v1/chat/completions", data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": BROWSER_UA,
        })
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.status == 200
    except urllib.error.HTTPError as error:
        text = error.read()[:300].decode("utf-8", "replace")
        # 403 pre-charge failure = low balance (insolvent); anything else is
        # treated as insolvent too — enabling only on a clean 200.
        print(f"  solvency probe HTTP {error.code}: {text[:160]}")
        return False
    except Exception as error:  # network noise: one retry
        print(f"  solvency probe error ({error}); retrying once")
        time.sleep(3)
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return response.status == 200
        except Exception as retry_error:
            print(f"  solvency probe retry failed: {retry_error}")
            return False


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
        "type": TYPE,
        "key": key,
        "base_url": BASE_URL,
        "models": MODELS,
        "model_mapping": MODEL_MAPPING,
        "group": "default",
        "test_model": TEST_MODEL,
        "priority": PRIORITY,
        "weight": WEIGHT,
        "status": 2,  # created disabled; enabled only after probes pass
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
        f"new-api-before-tabitoken-split-{time.strftime('%Y%m%d-%H%M%S')}.db"
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
    expected_status: int, strict: bool,
) -> None:
    """Readback-check one channel.

    strict=True (channels created by this run) also pins priority/weight and
    auto_ban. strict=False (pre-existing channels) checks identity fields
    only: Guardian's weight closed loop legitimately drifts posture on
    non-fixed routes, and a re-run must not fail on that drift.
    """
    channel = next((i for i in items if i.get("id") == channel_id), None)
    if channel is None:
        raise RuntimeError(f"ch{channel_id} missing on readback")
    expected = {
        "name": name,
        "type": TYPE,
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
    mapping = str(channel.get("model_mapping") or "")
    if json.loads(mapping or "null") != {"zg-claude-opus-5": "claude-opus-5"}:
        mismatch["model_mapping"] = (mapping, MODEL_MAPPING)

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

    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    keys = read_source_keys(db_path)
    token, user_id = smoke.admin_auth()
    headers = {
        "Authorization": f"Bearer {token}",
        "New-Api-User": str(user_id),
    }
    items = list_channels(smoke, headers)
    existing: dict[str, dict] = {}
    for name in CHANNEL_NAMES:
        named = [i for i in items if i.get("name") == name]
        named_ids = {int(i["id"]) for i in named if isinstance(i.get("id"), int)}
        if len(named_ids) > 1:
            raise RuntimeError(f"duplicate channel name {name!r}: {sorted(named_ids)}")
        if named_ids:
            existing[name] = next(i for i in named if isinstance(i.get("id"), int))
    max_id = max(
        (int(i["id"]) for i in items if isinstance(i.get("id"), int)), default=0
    )
    todo = [name for name in CHANNEL_NAMES if name not in existing]
    planned_ids = {name: max_id + n + 1 for n, name in enumerate(todo)}
    for index, name in enumerate(CHANNEL_NAMES):
        if name in existing:
            print(f"plan: {name} exists as ch{existing[name]['id']}; verify only")
        else:
            print(
                f"plan: create {name} as ch{planned_ids[name]} "
                f"(key#{index + 1}={mask(keys[index])}) disabled, solvency probe, "
                f"enable only if solvent at p{PRIORITY}/w{WEIGHT}"
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
    enabled: set[str] = set()
    try:
        for index, name in enumerate(CHANNEL_NAMES):
            key = keys[index]
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
                body={"mode": "single", "channel": channel_payload(name, key)},
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

            print(f"ch{channel_id} {name} solvency probe (key#{index + 1}, direct upstream)...")
            if not solvency_probe(key):
                # Insolvent key: keep the channel disabled. Topping up the
                # key + manual enable is the documented recovery path.
                print(f"ch{channel_id} {name} key#{index + 1} INSOLVENT (pre-charge 403); left disabled")
                continue
            management_probe(smoke, headers, channel_id)
            print(f"ch{channel_id} {name} management probe ok ({TEST_MODEL})")
            set_status(smoke, headers, channel_id, 1)
            enabled.add(name)
            print(f"ch{channel_id} {name} enabled at p{PRIORITY}/w{WEIGHT}")

        if enabled:
            print(f"waiting {CACHE_SYNC_SECONDS}s for channel cache sync")
            time.sleep(CACHE_SYNC_SECONDS)
            items = list_channels(smoke, headers)
        for name, channel_id in created_ids.items():
            strict = name in created_new
            if strict:
                expected_status = 1 if name in enabled else 2
            else:
                # Re-read the existing channel's status from the freshest
                # list: Guardian may flip it during the cache-sync window,
                # and a stale expectation would fail on spurious mismatch.
                current = next(
                    (i for i in items if i.get("id") == channel_id), existing[name]
                )
                status_value = current.get("status")
                if not isinstance(status_value, int):
                    status_value = existing[name].get("status")
                if not isinstance(status_value, int):
                    raise RuntimeError(f"ch{channel_id} status unavailable in API projection")
                expected_status = status_value
            verify(db_path, items, name, channel_id, expected_status, strict=strict)
        parts = []
        for name, cid in created_ids.items():
            if name in enabled:
                parts.append(f"ch{cid} {name} live at p{PRIORITY}/w{WEIGHT}")
            elif name in created_new:
                parts.append(f"ch{cid} {name} created DISABLED (insolvent key)")
            else:
                parts.append(f"ch{cid} {name} present, status untouched")
        print(f"OK: {', '.join(parts)}; backup={backup.name if backup else '(not needed)'}")
        if any(name in created_new and name not in enabled for name in created_ids):
            print("NOTE: disabled split channels are intentional (insolvent key); "
                  "add their ids to Guardian/smoke accepted-disabled lists if "
                  "they should stay parked until top-up.")
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
