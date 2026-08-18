#!/usr/bin/env python3
"""Create or refresh the isolated single-key OMP SOTA NewAPI channel.

The companion PowerShell wrapper supplies the key through an environment
variable. The key is never accepted as an argument or emitted in output.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any


SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
KEY_ENV = "CC_SWITCH_SOTA_KEY"
CHANNEL_NAME = "omp-sota-sotamodel"
BASE_URL = "https://www.sotamodel.net"
BASE_MODEL = "claude-opus-5"
MARKED_MODEL = "omp-sota-claude-opus-5"
MODELS = f"{BASE_MODEL},{MARKED_MODEL}"
MODEL_MAPPING = json.dumps({MARKED_MODEL: BASE_MODEL}, separators=(",", ":"))


def load_smoke() -> Any:
    spec = importlib.util.spec_from_file_location("newapi_local_smoke_for_sota_create", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load newapi-local-smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        f"new-api-before-{CHANNEL_NAME}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    )
    if destination.exists():
        raise RuntimeError(f"backup already exists: {destination.name}")
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as source, closing(
        sqlite3.connect(destination, timeout=30)
    ) as target:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RuntimeError("backup integrity check failed")
    if destination.stat().st_size <= 0:
        raise RuntimeError("backup is empty")
    return destination


def set_status(smoke: Any, headers: dict[str, str], channel_id: int, status: int) -> None:
    response_status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}/status",
        method="POST",
        body={"status": status},
        headers=headers,
    )
    if response_status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"channel status update failed HTTP {response_status}")


def list_channels(smoke: Any, headers: dict[str, str]) -> list[dict[str, Any]]:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=300", headers=headers
    )
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"channel list failed HTTP {status}")
    items = body.get("data") or []
    if isinstance(items, dict):
        items = items.get("items") or []
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise RuntimeError("channel list shape invalid")
    return items


def payload(key: str, channel_id: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": CHANNEL_NAME,
        "type": 14,
        "key": key,
        "base_url": BASE_URL,
        "models": MODELS,
        "group": "default",
        "model_mapping": MODEL_MAPPING,
        "test_model": BASE_MODEL,
        "priority": 1,
        "weight": 1,
        "auto_ban": 1,
    }
    if channel_id is not None:
        result["id"] = channel_id
    return result


def hydrate_key(db_path: Path, channel_id: int, channel: dict[str, Any]) -> dict[str, Any]:
    key = channel.get("key")
    if isinstance(key, str) and key.strip() and "*" not in key:
        return channel
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        row = connection.execute(
            "SELECT key FROM channels WHERE id = ?", (channel_id,)
        ).fetchone()
    actual = row[0] if row else None
    if not isinstance(actual, str) or not actual.strip():
        raise RuntimeError(f"ch{channel_id} key unavailable in local SSOT")
    return {**channel, "key": actual}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    key = str(os.environ.get(KEY_ENV) or "").strip()
    if not key or "\n" in key or "\r" in key:
        print("refused: SOTA key input is empty or multiline")
        return 1

    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}
    items = list_channels(smoke, headers)
    matches = [item for item in items if item.get("name") == CHANNEL_NAME]
    if len(matches) > 1:
        print("refused: multiple isolated SOTA channels found")
        return 1
    existing = matches[0] if matches else None
    if not args.apply:
        print(
            f"dry-run: isolated single-key channel name={CHANNEL_NAME} "
            f"existing={str(existing is not None).lower()} baseUrl={BASE_URL}"
        )
        return 0

    backup = online_backup(db_path)
    channel_id: int | None = int(existing["id"]) if existing else None
    original: dict[str, Any] | None = None
    try:
        if existing is None:
            status, body = smoke.http_json(
                f"{smoke.NEWAPI_BASE}/api/channel/",
                method="POST",
                body={"mode": "single", "channel": payload(key)},
                headers=headers,
            )
            if status != 200 or not isinstance(body, dict) or not body.get("success"):
                raise RuntimeError(f"channel create failed HTTP {status}")
            items = list_channels(smoke, headers)
            created = next((item for item in items if item.get("name") == CHANNEL_NAME), None)
            if created is None or not isinstance(created.get("id"), int):
                raise RuntimeError("created SOTA channel missing on readback")
            channel_id = int(created["id"])
            set_status(smoke, headers, channel_id, 2)
            print(f"created: isolated SOTA channel ch{channel_id} disabled")
        else:
            channel_id = int(existing["id"])
            get_status, get_body = smoke.http_json(
                f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}", headers=headers
            )
            fetched = get_body.get("data") if isinstance(get_body, dict) else None
            if get_status != 200 or not isinstance(fetched, dict):
                raise RuntimeError(f"channel read failed HTTP {get_status}")
            original = {
                key_name: value for key_name, value in fetched.items() if key_name != "status"
            }
            original = hydrate_key(db_path, channel_id, original)
            updated = payload(key, channel_id)
            status, body = smoke.http_json(
                f"{smoke.NEWAPI_BASE}/api/channel/",
                method="PUT",
                body=updated,
                headers=headers,
            )
            if status != 200 or not isinstance(body, dict) or not body.get("success"):
                raise RuntimeError(f"channel refresh failed HTTP {status}")
            set_status(smoke, headers, channel_id, 2)
            print(f"refreshed: isolated SOTA channel ch{channel_id} disabled")

        assert channel_id is not None
        probe_status, probe_body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={BASE_MODEL}",
            headers=headers,
            timeout=90,
        )
        if probe_status != 200 or not isinstance(probe_body, dict) or not probe_body.get("success"):
            raise RuntimeError(f"management probe failed HTTP {probe_status}")
        set_status(smoke, headers, channel_id, 1)
        print(f"ready: ch{channel_id} managementProbe=200 enabled=true backup={backup.name}")
        return 0
    except Exception:
        if channel_id is not None:
            try:
                set_status(smoke, headers, channel_id, 2)
            except Exception:
                pass
        if original is not None:
            try:
                status, body = smoke.http_json(
                    f"{smoke.NEWAPI_BASE}/api/channel/",
                    method="PUT",
                    body=original,
                    headers=headers,
                )
                if status == 200 and isinstance(body, dict) and body.get("success"):
                    set_status(smoke, headers, int(original["id"]), 2)
            except Exception:
                pass
        print(f"failed: isolated SOTA change rolled back where possible backup={backup.name}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
