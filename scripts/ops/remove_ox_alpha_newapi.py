#!/usr/bin/env python3
"""Remove all Ox Alpha models from NewAPI (2026-08-27: free tier cancelled,
paid Ox Alpha also gone per user).

Strips the public Ox model ids from every channel that still lists them:
- ch96  opencode-zen-free  (shared free pool; models -= x-preview-f-free)
- ch110 yjs-free           (shared free pool; models -= x-preview-f-free,
                            model_mapping key dropped)
- ch109 imagic             (shared paid pool; models -= x-preview-f)
A channel whose model list becomes empty is deleted instead of updated.
Orphaned ModelRatio entries for the Ox public ids are removed.

What --apply does:
- whole-DB SQLite snapshot backup
- PUT /api/channel/ with models/model_mapping minus Ox (full channel object
  minus status; the fork syncs abilities on update), or DELETE /api/channel/<id>
  when the channel would end up empty
- delete orphaned ModelRatio entries
- readback verification: Ox models gone from every channel, abilities rows
  gone or disabled, ratio entries removed

Rollback on failure: PUT/POST the original channel payloads back and restore
the original ModelRatio option. Re-running is idempotent (verify-only once
everything is already gone).
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

MODEL_RATIO_OPTION = "ModelRatio"
OX_PUBLIC = {"x-preview-f-free", "x-preview-f", "ox-alpha", "ox-alpha-free"}
OX_UPSTREAM = {"ox-alpha", "stealth/ox-alpha", "ox-alpha-free"}


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
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


def fetch_channels(smoke, headers: dict[str, str]) -> list[dict]:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=200", headers=headers
    )
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"channel list failed: HTTP {status}")
    items = body.get("data") or []
    if isinstance(items, dict):
        items = items.get("items") or []
    return [i for i in items if isinstance(i, dict)]


def put_channel(smoke, headers: dict[str, str], payload: dict) -> None:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/",
        method="PUT",
        body=payload,
        headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"channel PUT failed: HTTP {status} message={message!r}")


def post_channel(smoke, headers: dict[str, str], payload: dict) -> None:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/",
        method="POST",
        body=payload,
        headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"channel POST failed: HTTP {status} message={message!r}")


def delete_channel(smoke, headers: dict[str, str], channel_id: int) -> None:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}",
        method="DELETE",
        headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(
            f"channel DELETE failed: HTTP {status} message={message!r}"
        )


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        f"new-api-before-ox-alpha-remove-{time.strftime('%Y%m%d-%H%M%S')}.db"
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


def get_option_db(db_path: Path, key: str) -> str:
    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        row = connection.execute(
            "SELECT value FROM options WHERE key = ?", (key,)
        ).fetchone()
    if row is None or not isinstance(row[0], str):
        raise RuntimeError(f"option {key!r} is missing")
    return row[0]


def put_option(smoke, headers: dict[str, str], key: str, value: str) -> None:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/option/",
        method="PUT",
        body={"key": key, "value": value},
        headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"option {key!r} update failed: HTTP {status}")


def strip_mapping(raw: str) -> tuple[str, bool]:
    if not raw or not raw.strip():
        return raw, False
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError:
        return raw, False
    if not isinstance(mapping, dict):
        return raw, False
    cleaned = {
        key: value
        for key, value in mapping.items()
        if key not in OX_PUBLIC and value not in OX_UPSTREAM
    }
    if len(cleaned) == len(mapping):
        return raw, False
    return json.dumps(cleaned, separators=(",", ":"), sort_keys=True), True


def plan_channel(channel: dict) -> dict | None:
    models = [m for m in str(channel.get("models") or "").split(",") if m]
    kept = [m for m in models if m not in OX_PUBLIC]
    mapping, mapping_changed = strip_mapping(str(channel.get("model_mapping") or ""))
    ox_here = [m for m in models if m in OX_PUBLIC]
    if not ox_here and not mapping_changed:
        return None
    return {
        "id": int(channel["id"]),
        "name": channel.get("name"),
        "ox_models": ox_here,
        "kept_models": kept,
        "delete": not kept,
        "new_models": ",".join(kept),
        "mapping_changed": mapping_changed,
        "new_mapping": mapping,
    }


def verify(db_path: Path, channels: list[dict], ratio_json: str) -> None:
    for channel in channels:
        models = [m for m in str(channel.get("models") or "").split(",") if m]
        leftover = [m for m in models if m in OX_PUBLIC]
        if leftover:
            raise RuntimeError(
                f"ch{channel['id']} still lists Ox models: {leftover}"
            )
        mapping_raw = str(channel.get("model_mapping") or "")
        if mapping_raw.strip():
            mapping = json.loads(mapping_raw)
            bad = [
                k
                for k, v in mapping.items()
                if k in OX_PUBLIC or v in OX_UPSTREAM
            ]
            if bad:
                raise RuntimeError(f"ch{channel['id']} mapping still Ox: {bad}")
    ratios = json.loads(ratio_json)
    bad_ratio = [k for k in ratios if k in OX_PUBLIC]
    if bad_ratio:
        raise RuntimeError(f"ModelRatio still has Ox entries: {bad_ratio}")
    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        rows = connection.execute(
            "SELECT channel_id, model FROM abilities WHERE model IN ({})"
            " AND enabled != 0".format(",".join("?" for _ in OX_PUBLIC)),
            tuple(OX_PUBLIC),
        ).fetchall()
    if rows:
        raise RuntimeError(f"abilities still enabled for Ox models: {rows}")


def main() -> int:
    args = parse_args()
    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    token, user_id = smoke.admin_auth()
    headers = {
        "Authorization": f"Bearer {token}",
        "New-Api-User": str(user_id),
    }

    channels = fetch_channels(smoke, headers)
    plans = [p for p in (plan_channel(c) for c in channels) if p]
    by_id = {int(c["id"]): c for c in channels}
    original_ratio = get_option_db(db_path, MODEL_RATIO_OPTION)
    ratio_keys = [k for k in json.loads(original_ratio) if k in OX_PUBLIC]

    if not plans and not ratio_keys:
        print("plan: no Ox models left in any channel; verify only")
    for plan in plans:
        action = "DELETE" if plan["delete"] else "strip"
        print(
            f"plan: ch{plan['id']} {plan['name']} {action} "
            f"ox={plan['ox_models']} kept={len(plan['kept_models'])} "
            f"mapping_changed={plan['mapping_changed']}"
        )
    if ratio_keys:
        print(f"plan: ModelRatio -= {ratio_keys}")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    changed: list[tuple[dict, dict]] = []
    deleted: list[tuple[int, dict]] = []
    ratio_changed = False
    try:
        for plan in plans:
            original = by_id[plan["id"]]
            payload = {k: v for k, v in original.items() if k != "status"}
            if plan["delete"]:
                delete_channel(smoke, headers, plan["id"])
                deleted.append((plan["id"], payload))
                print(f"ch{plan['id']} deleted")
            else:
                updated = dict(payload)
                updated["models"] = plan["new_models"]
                if plan["mapping_changed"]:
                    updated["model_mapping"] = plan["new_mapping"]
                put_channel(smoke, headers, updated)
                changed.append((plan["id"], payload))
                print(f"ch{plan['id']} models: {len(plan['ox_models']) + len(plan['kept_models'])} -> {len(plan['kept_models'])}")
        if ratio_keys:
            ratios = json.loads(original_ratio)
            for key in ratio_keys:
                ratios.pop(key, None)
            put_option(
                smoke,
                headers,
                MODEL_RATIO_OPTION,
                json.dumps(ratios, separators=(",", ":"), sort_keys=True),
            )
            ratio_changed = True
            print(f"ModelRatio entries removed: {ratio_keys}")

        readback = fetch_channels(smoke, headers)
        readback_ratio = get_option_db(db_path, MODEL_RATIO_OPTION)
        verify(db_path, readback, readback_ratio)
        print(f"OK: Ox Alpha removed from NewAPI; backup={backup.name}")
        return 0
    except Exception:
        for channel_id, payload in changed:
            try:
                put_channel(smoke, headers, payload)
                print(f"rollback: ch{channel_id} restored")
            except Exception as error:
                print(f"rollback warning: ch{channel_id} restore failed: {error}")
        for channel_id, payload in deleted:
            # 重建必须拿新 id：POST 不得携带原 id（server 管理字段）
            recreate = {k: v for k, v in payload.items() if k != "id"}
            try:
                post_channel(smoke, headers, recreate)
                print(f"rollback: ch{channel_id} recreated (new id assigned)")
            except Exception as error:
                print(f"rollback warning: ch{channel_id} recreate failed: {error}")
        if ratio_changed:
            try:
                put_option(smoke, headers, MODEL_RATIO_OPTION, original_ratio)
            except Exception as error:
                print(f"rollback warning: could not restore ModelRatio: {error}")
        print(f"rollback attempted; full snapshot={backup.name}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
