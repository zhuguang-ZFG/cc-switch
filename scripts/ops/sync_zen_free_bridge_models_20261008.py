#!/usr/bin/env python3
"""Sync ch178 zen-free-bridge model list to the current /zen/v1/models lineup.

2026-10-08: the public zen model list changed since the channel was created:
- mimo-v2.5-free is gone (not advertised upstream anymore)
- muse-spark-1.2-contributor-free is now advertised (was only on disabled ch96)

This syncs ch178 to the canonical 12-model free list in
add_zen_free_bridge_channel.py (MODELS), removes the orphaned ModelRatio entry
for mimo-v2.5-free, ensures ModelRatio=0 for muse-spark-1.2-contributor-free,
and verifies readback (models string + abilities + ratio). The channel stays
enabled throughout (models update is additive/removal only, no status flip).

What --apply does:
- whole-DB SQLite snapshot backup
- PUT /api/channel/ with the canonical models string (full object minus status)
- ModelRatio: drop mimo-v2.5-free entry; add muse-spark-1.2-contributor-free=0
- readback verification: channel.models == canonical, abilities rows for the
  12 models enabled, ratio entries present/absent as expected

Rollback on failure: PUT the original channel payload back and restore the
original ModelRatio option. Re-running is idempotent (verify-only once synced).
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
BRIDGE_SCRIPT = Path(__file__).with_name("add_zen_free_bridge_channel.py")

CHANNEL_NAME = "zen-free-bridge"
MODEL_RATIO_OPTION = "ModelRatio"
FREE_MODEL_RATIO = 0


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
def load_bridge_models() -> tuple[str, ...]:
    spec = importlib.util.spec_from_file_location("add_zen_free_bridge_channel", BRIDGE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {BRIDGE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(module.MODELS)



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the backed-up live change; default is read-only",
    )
    return parser.parse_args()


def fetch_channel(smoke, headers: dict[str, str]) -> dict:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=200", headers=headers
    )
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"channel list failed: HTTP {status}")
    items = body.get("data") or []
    if isinstance(items, dict):
        items = items.get("items") or []
    matches = [i for i in items if isinstance(i, dict) and i.get("name") == CHANNEL_NAME]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {CHANNEL_NAME!r}, got {len(matches)}")
    return matches[0]


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


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        f"new-api-before-zen-free-bridge-sync-{time.strftime('%Y%m%d-%H%M%S')}.db"
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


def merge_ratio(current: str, removed: tuple[str, ...], added: dict[str, int]) -> str:
    try:
        ratios = json.loads(current)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{MODEL_RATIO_OPTION} is invalid JSON") from error
    if not isinstance(ratios, dict):
        raise RuntimeError(f"{MODEL_RATIO_OPTION} must be a JSON object")
    for model in removed:
        ratios.pop(model, None)
    ratios.update(added)
    return json.dumps(ratios, separators=(",", ":"), sort_keys=True)


def verify(db_path: Path, channel: dict, expected_models: str, removed: tuple[str, ...], added: tuple[str, ...]) -> None:
    if channel.get("models") != expected_models:
        raise RuntimeError(
            f"readback models mismatch: {channel.get('models')!r} != {expected_models!r}"
        )
    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        for model in expected_models.split(","):
            ability = connection.execute(
                "SELECT enabled FROM abilities WHERE channel_id = ? AND model = ?",
                (int(channel["id"]), model),
            ).fetchone()
            if ability is None or ability[0] != 1:
                raise RuntimeError(f"abilities row for {model} not enabled")
        ratio_row = connection.execute(
            "SELECT value FROM options WHERE key = ?", (MODEL_RATIO_OPTION,)
        ).fetchone()
    if ratio_row is None:
        raise RuntimeError(f"{MODEL_RATIO_OPTION} missing on readback")
    ratios = json.loads(ratio_row[0])
    for model in added:
        if ratios.get(model) != FREE_MODEL_RATIO:
            raise RuntimeError(f"ModelRatio for {model} != {FREE_MODEL_RATIO} on readback")
    for model in removed:
        if model in ratios:
            raise RuntimeError(f"ModelRatio entry for {model} still present")


def main() -> int:
    args = parse_args()
    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    models = load_bridge_models()
    expected_models = ",".join(models)
    token, user_id = smoke.admin_auth()
    headers = {
        "Authorization": f"Bearer {token}",
        "New-Api-User": str(user_id),
    }

    channel = fetch_channel(smoke, headers)
    channel_id = int(channel["id"])
    current_models = str(channel.get("models") or "")
    current_list = [m for m in current_models.split(",") if m]
    removed = tuple(m for m in current_list if m not in models)
    added = tuple(m for m in models if m not in current_list)
    original_ratio = get_option_db(db_path, MODEL_RATIO_OPTION)
    if not removed and not added:
        print(f"plan: ch{channel_id} {CHANNEL_NAME} models already canonical "
              f"({len(models)} models); verify only")
    else:
        print(
            f"plan: ch{channel_id} {CHANNEL_NAME} models += {list(added) or 'none'} "
            f"-={list(removed) or 'none'}"
        )
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    original_payload = {k: v for k, v in channel.items() if k != "status"}
    ratio_changed = False
    try:
        if removed or added:
            updated_payload = dict(original_payload)
            updated_payload["models"] = expected_models
            put_channel(smoke, headers, updated_payload)
            print(f"ch{channel_id} models -> {expected_models}")

        target_ratio = merge_ratio(original_ratio, removed, {m: FREE_MODEL_RATIO for m in added})
        if target_ratio != original_ratio:
            put_option(smoke, headers, MODEL_RATIO_OPTION, target_ratio)
            ratio_changed = True
            print(f"ModelRatio synced (removed={list(removed)}, added={list(added)})")

        readback = fetch_channel(smoke, headers)
        verify(db_path, readback, expected_models, removed, added)
        print(f"OK: ch{channel_id} {CHANNEL_NAME} models synced to {len(models)}; "
              f"backup={backup.name}")
        return 0
    except Exception:
        if removed or added:
            put_channel(smoke, headers, original_payload)
            print("rollback: channel payload restored")
        if ratio_changed:
            put_option(smoke, headers, MODEL_RATIO_OPTION, original_ratio)
            print("rollback: ModelRatio restored")
        print(f"rollback attempted; full snapshot={backup.name}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())