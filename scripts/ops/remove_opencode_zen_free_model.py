#!/usr/bin/env python3
"""Remove a dead model from ch96 opencode-zen-free (generic version).

First used 2026-08-21 for deepseek-v4-flash-free (free promotion ended,
upstream hard 401 ModelError) and laguna-s-2.1-free (upstream 503
"Endpoint is unavailable" since 2026-08-21, never registered in OMP).

A model listed by /v1/models but hard-failing every call only wastes relay
attempts whenever NewAPI picks it. Quota-style 429 FreeUsageLimitError is NOT
a removal reason — that self-heals when the daily quota resets.

What --apply does:
- whole-DB SQLite snapshot backup
- PUT /api/channel/ with models minus the target (full channel object minus
  status; the fork syncs abilities on update)
- delete the now-orphaned ModelRatio entry if present
- readback verification: model gone from channel.models, abilities row gone
  or disabled, ratio entry removed

Rollback on failure: PUT the original channel payload back and restore the
original ModelRatio option. Re-running is idempotent (verify-only once the
model is already gone).
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

CHANNEL_NAME = "opencode-zen-free"
MODEL_RATIO_OPTION = "ModelRatio"


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", help="model id to remove from ch96")
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


def online_backup(db_path: Path, model: str) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() else "-" for c in model)
    destination = backup_dir / (
        f"new-api-before-zen-remove-{safe}-{time.strftime('%Y%m%d-%H%M%S')}.db"
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


def remove_ratio_entry(current: str, model: str) -> str:
    try:
        ratios = json.loads(current)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{MODEL_RATIO_OPTION} is invalid JSON") from error
    if not isinstance(ratios, dict):
        raise RuntimeError(f"{MODEL_RATIO_OPTION} must be a JSON object")
    ratios.pop(model, None)
    return json.dumps(ratios, separators=(",", ":"), sort_keys=True)


def verify(db_path: Path, channel: dict, expected_models: str, model: str) -> None:
    if channel.get("models") != expected_models:
        raise RuntimeError(
            f"readback models mismatch: {channel.get('models')!r} != {expected_models!r}"
        )
    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        ability = connection.execute(
            "SELECT enabled FROM abilities WHERE channel_id = ? AND model = ?",
            (int(channel["id"]), model),
        ).fetchone()
        ratio_row = connection.execute(
            "SELECT value FROM options WHERE key = ?", (MODEL_RATIO_OPTION,)
        ).fetchone()
    if ability is not None and ability[0] != 0:
        raise RuntimeError(f"abilities row for {model} still enabled")
    if ratio_row is None:
        raise RuntimeError(f"{MODEL_RATIO_OPTION} missing on readback")
    ratios = json.loads(ratio_row[0])
    if model in ratios:
        raise RuntimeError(f"ModelRatio entry for {model} still present")


def main() -> int:
    args = parse_args()
    model = args.model.strip()
    if not model:
        raise RuntimeError("model must not be empty")
    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    token, user_id = smoke.admin_auth()
    headers = {
        "Authorization": f"Bearer {token}",
        "New-Api-User": str(user_id),
    }

    channel = fetch_channel(smoke, headers)
    channel_id = int(channel["id"])
    current_models = str(channel.get("models") or "")
    models_list = [m for m in current_models.split(",") if m]
    already_gone = model not in models_list
    updated_models = ",".join(m for m in models_list if m != model)
    original_ratio = get_option_db(db_path, MODEL_RATIO_OPTION)
    ratio_has_entry = model in json.loads(original_ratio)
    if already_gone and not ratio_has_entry:
        print(f"plan: {model} already fully removed from ch{channel_id}; "
              f"verify only")
    else:
        print(
            f"plan: ch{channel_id} {CHANNEL_NAME} models -= {model} "
            f"(present={not already_gone}, ratio_entry={ratio_has_entry})"
        )
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(db_path, model)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    channel_changed = False
    ratio_changed = False
    original_payload = {k: v for k, v in channel.items() if k != "status"}
    try:
        if not already_gone:
            updated_payload = dict(original_payload)
            updated_payload["models"] = updated_models
            put_channel(smoke, headers, updated_payload)
            channel_changed = True
            print(f"ch{channel_id} models updated ({len(models_list)} -> "
                  f"{len(models_list) - 1})")
        if ratio_has_entry:
            put_option(smoke, headers, MODEL_RATIO_OPTION,
                       remove_ratio_entry(original_ratio, model))
            ratio_changed = True
            print(f"ModelRatio entry removed for {model}")

        readback = fetch_channel(smoke, headers)
        verify(db_path, readback, updated_models, model)
        print(f"OK: {model} removed from ch{channel_id}; "
              f"backup={backup.name}")
        return 0
    except Exception:
        if channel_changed:
            try:
                put_channel(smoke, headers, original_payload)
                print(f"rollback: ch{channel_id} models restored")
            except Exception as error:
                print(f"rollback warning: channel restore failed: {error}")
        if ratio_changed:
            try:
                put_option(smoke, headers, MODEL_RATIO_OPTION, original_ratio)
            except Exception as error:
                print(f"rollback warning: could not restore ModelRatio: {error}")
        print(f"rollback attempted; full snapshot={backup.name}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
