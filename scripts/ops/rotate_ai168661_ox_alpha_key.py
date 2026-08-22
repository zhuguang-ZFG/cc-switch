#!/usr/bin/env python3
"""Rotate the ox-alpha family key on ch102 (ai-168661-ox-alpha).

Context 2026-08-22: ai.168661.xyz re-issued its ox-alpha family key. The old
key (onboarded 2026-08-21 by add_ai168661_ox_alpha_channel.py) now returns
401 Invalid token on the upstream management probe. The site contract is one
key per model family, so this is a key ROTATION on the existing channel, not
a second channel — the channel keeps its name, models mapping
(x-preview-f-free -> ox-alpha), priority/weight, status and header_override;
only `key` changes.

Workflow contract (same safety posture as add_ai168661_ox_alpha_channel.py):
dup/name check, whole-DB snapshot backup, PUT update with all fields pinned
from the live channel projection (key replaced), management probe while the
channel keeps its current status (exercises the mapping end-to-end), readback
verify that nothing but the key changed. New key from argv, never printed.
Re-running with the same key is idempotent (probe + verify only).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")

CHANNEL_NAME = "ai-168661-ox-alpha"
BASE_URL = "https://ai.168661.xyz"
PUBLIC_MODEL = "x-preview-f-free"
UPSTREAM_MODEL = "ox-alpha"
MODELS = PUBLIC_MODEL
MODEL_MAPPING = json.dumps({PUBLIC_MODEL: UPSTREAM_MODEL})
PRIORITY = 7
WEIGHT = 5
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HEADER_OVERRIDE = json.dumps({"User-Agent": BROWSER_UA})
OMP_MODELS_YML = Path.home() / ".omp" / "agent" / "models.yml"


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
    parser.add_argument("key", help="new ai.168661 ox-alpha family key (never printed)")
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
        raise RuntimeError("channel list page full (>=200); paginate before use")
    return items


def management_probe(smoke, headers: dict[str, str], channel_id: int) -> None:
    """Probe the channel directly; exercises key + model_mapping upstream."""
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={PUBLIC_MODEL}",
        headers=headers,
        timeout=100,  # 168661 cold non-stream once exceeded 90s
    )
    if status == 200 and isinstance(body, dict) and body.get("success"):
        return
    message = body.get("message") if isinstance(body, dict) else None
    raise RuntimeError(f"management probe failed: HTTP {status} message={message!r}")


def read_omp_relay_token() -> str:
    text = OMP_MODELS_YML.read_text(encoding="utf-8")
    match = re.search(r"^\s*apiKey:\s*(sk-\S+)\s*$", text, re.MULTILINE)
    if match is None:
        raise RuntimeError(f"no sk- apiKey found in {OMP_MODELS_YML}")
    return match.group(1)


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        f"new-api-before-168661-key-rotate-{time.strftime('%Y%m%d-%H%M%S')}.db"
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


def main() -> int:
    args = parse_args()
    key = args.key.strip()
    if not key:
        raise RuntimeError("key must not be empty")

    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    token, user_id = smoke.admin_auth()
    headers = {
        "Authorization": f"Bearer {token}",
        "New-Api-User": str(user_id),
    }
    items = list_channels(smoke, headers)
    named = [i for i in items if i.get("name") == CHANNEL_NAME]
    named_ids = {int(i["id"]) for i in named if isinstance(i.get("id"), int)}
    if len(named_ids) != 1:
        raise RuntimeError(
            f"expected exactly one channel named {CHANNEL_NAME!r}, "
            f"got {sorted(named_ids)}"
        )
    channel = next(i for i in named if isinstance(i.get("id"), int))
    channel_id = int(channel["id"])
    status_now = channel.get("status")
    if not isinstance(status_now, int):
        raise RuntimeError(f"ch{channel_id} status unavailable in API projection")

    print(
        f"plan: rotate key on ch{channel_id} {CHANNEL_NAME} "
        f"(new key={mask(key)}); name/models/mapping/p{PRIORITY}/w{WEIGHT}/"
        f"status={status_now} pinned unchanged"
    )
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    # UpdateChannel binds the channel struct from the body directly (unlike
    # AddChannel's {"mode", "channel"} wrapper); wrapping yields
    # "record not found" because id lands on the wrong level. A hand-picked
    # subset yields "Invalid parameters" — the handler expects the full
    # channel projection as returned by the list API (minus status, per the
    # established scripts/ops pattern), so pin every field from live state
    # and replace only the key.
    payload = {k: v for k, v in channel.items() if k != "status"}
    payload["key"] = key
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/",
        method="PUT",
        body=payload,
        headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"update failed: HTTP {status} message={message!r}")
    print(f"ch{channel_id} key rotated")

    management_probe(smoke, headers, channel_id)
    print(f"ch{channel_id} management probe ok ({PUBLIC_MODEL}->{UPSTREAM_MODEL})")

    # Readback verify: nothing but the key may have drifted.
    items = list_channels(smoke, headers)
    back = next((i for i in items if i.get("id") == channel_id), None)
    if back is None:
        raise RuntimeError(f"ch{channel_id} missing on readback")
    expected = {
        "name": CHANNEL_NAME,
        "type": 1,
        "status": status_now,
        "base_url": BASE_URL,
        "models": MODELS,
        "test_model": PUBLIC_MODEL,
        "auto_ban": 1,
        "priority": PRIORITY,
        "weight": WEIGHT,
    }
    mismatch = {
        field: (back.get(field), value)
        for field, value in expected.items()
        if back.get(field) != value
    }
    try:
        mapping_ok = json.loads(str(back.get("model_mapping") or "null")) == {
            PUBLIC_MODEL: UPSTREAM_MODEL
        }
    except json.JSONDecodeError:
        mapping_ok = False
    if not mapping_ok:
        mismatch["model_mapping"] = ("drifted", MODEL_MAPPING)
    try:
        header_ok = json.loads(str(back.get("header_override") or "null")) == {
            "User-Agent": BROWSER_UA
        }
    except json.JSONDecodeError:
        header_ok = False
    if not header_ok:
        mismatch["header_override"] = ("drifted", "expected")

    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        ability = connection.execute(
            "SELECT enabled FROM abilities WHERE channel_id = ? AND model = ?",
            (channel_id, PUBLIC_MODEL),
        ).fetchone()
    ability_expected = 1 if status_now == 1 else 0
    abilities_ok = ability is not None and ability[0] == ability_expected
    if mismatch or not abilities_ok:
        raise RuntimeError(
            f"readback mismatch for ch{channel_id}: "
            f"channel={mismatch or 'ok'} abilities_ok={abilities_ok}"
        )
    print(
        f"OK: ch{channel_id} {CHANNEL_NAME} key rotated, probe green, "
        f"all other fields verified unchanged; backup={backup.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
