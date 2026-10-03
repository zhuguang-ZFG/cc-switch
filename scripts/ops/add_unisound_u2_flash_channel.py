#!/usr/bin/env python3
"""Onboard Unisound MaaS u2-flash into the local NewAPI fork (2026-10-02).

Background
----------
User supplied a Unisound (云知声) MaaS key and the launch notice:
"U2 Flash 全新上线！限时免费无限量调用（2026.09.30-2026.10.31）",
base URL ``https://maas-api.unisound.com/v1``.
Direct probes with the user key (key handled via env var only — never
written to the repo, logs, or docs; on --apply it is POSTed to NewAPI and
persisted as the channel credential in the local NewAPI DB, which is the
SSOT for channel keys):

- ``GET /v1/models`` -> 22 models (``u2-flash``, ``u2``, ``u2-med`` plus
  third-party relay ids: glm/qwen/kimi/deepseek/MiniMax families);
- ``u2-flash`` chat 200, reasoning model (``reasoning_content`` burns the
  completion budget: max_tokens=64 -> finish=length with only 'p' visible);
- streaming works: reasoning + content deltas, finish=stop (17 chunks);
- ``u2`` also live (finish=stop 'pong') but its pricing is NOT announced —
  only ``u2-flash`` is in the free window, so only ``u2-flash`` is onboarded
  with ModelRatio=0. Revisit ``u2`` when pricing is known.

Free window ends 2026-10-31: after that the channel may start failing or
billing — re-verify ModelRatio/posture before November.

Usage: ``UNISOUND_KEY=sk-... python3 add_unisound_u2_flash_channel.py [--apply]``
Key comes from the env var only; it is never printed, logged, or written
to the repo. Dry-run by default.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
DB_PATH = Path.home() / ".new-api-local" / "new-api.db"
GATEWAY_BASE = "http://127.0.0.1:3002"

MODEL = "u2-flash"
CHANNEL_NAME = "unisound-u2-flash"
BASE_URL = "https://maas-api.unisound.com"
PRIORITY = 0
WEIGHT = 2


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load smoke helper: {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def db_one(sql, params=()):
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as c:
        return c.execute(sql, params).fetchone()


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        f"new-api-before-unisound-u2-flash-{time.strftime('%Y%m%d-%H%M%S')}.db"
    )
    source = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
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


def fetch_channels(smoke, headers):
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=200", headers=headers
    )
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"channel list failed: HTTP {status}")
    items = body.get("data") or []
    if isinstance(items, dict):
        items = items.get("items") or []
    return [i for i in items if isinstance(i, dict)]


def read_gateway_key() -> str:
    """OMP zg-newapi apiKey from live models.yml (never printed)."""
    import re

    text = (Path.home() / ".omp" / "agent" / "models.yml").read_text(encoding="utf-8")
    match = re.search(r"^  zg-newapi:\n(?:    .*\n)*?    apiKey:\s*(\S+)", text, flags=re.M)
    if not match:
        raise RuntimeError("zg-newapi apiKey not found in live models.yml")
    return match.group(1)


def wait_abilities(channel_id: int, model: str, seconds: int = 90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        row = db_one(
            "SELECT [group], enabled, priority, weight FROM abilities WHERE channel_id = ? AND model = ?",
            (channel_id, model),
        )
        if row:
            return row
        time.sleep(2)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    args = parser.parse_args()

    key = os.environ.get("UNISOUND_KEY", "").strip()
    if not key:
        raise RuntimeError("UNISOUND_KEY env var required")

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    channels = fetch_channels(smoke, headers)
    by_name = {str(c.get("name")): c for c in channels}
    if CHANNEL_NAME in by_name:
        raise RuntimeError(f"channel name already exists: {CHANNEL_NAME} (ch{by_name[CHANNEL_NAME]['id']})")
    live = db_one("SELECT channel_id FROM abilities WHERE model = ? AND enabled = 1", (MODEL,))
    if live:
        raise RuntimeError(f"{MODEL} already has an enabled ability: ch{live[0]}")

    print("plan:")
    print(f"  create: {CHANNEL_NAME} models={MODEL} prio={PRIORITY} w={WEIGHT} base={BASE_URL}")
    print(f"  ModelRatio[{MODEL}] = 0 (free window 2026.09.30-2026.10.31); POST /api/channel/fix")
    print("  functional: gateway chat (max_tokens=512, reasoning budget) + log attribution")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    # 1. create channel
    payload = {
        "name": CHANNEL_NAME,
        "type": 1,
        "base_url": BASE_URL,
        "key": key,
        "models": MODEL,
        "group": "default",
        "priority": PRIORITY,
        "weight": WEIGHT,
        "auto_ban": 1,
        "test_model": MODEL,
    }
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/",
        method="POST",
        body={"mode": "single", "channel": payload},
        headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"channel POST failed: HTTP {status} message={message!r}")
    after = fetch_channels(smoke, headers)
    match = [c for c in after if str(c.get("name")) == CHANNEL_NAME]
    if not match:
        raise RuntimeError(f"{CHANNEL_NAME} not visible after POST")
    new_id = int(match[0]["id"])
    print(f"created ch{new_id} {CHANNEL_NAME}")

    # 2. ModelRatio = 0 for the free window
    row = db_one("SELECT value FROM options WHERE key = 'ModelRatio'")
    ratios = json.loads(row[0]) if row and row[0] else {}
    ratios[MODEL] = 0
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/option/", method="PUT",
        body={"key": "ModelRatio", "value": json.dumps(ratios, separators=(",", ":"), sort_keys=True)},
        headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"ModelRatio update failed: HTTP {status} message={message!r}")
    print(f"ModelRatio[{MODEL}] = 0")

    # 3. fix + verify abilities
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/fix", method="POST", body={}, headers=headers
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"channel fix failed: HTTP {status} message={message!r}")
    row_ab = wait_abilities(new_id, MODEL)
    if not row_ab or tuple(row_ab) != ("default", 1, PRIORITY, WEIGHT):
        raise RuntimeError(f"abilities ch{new_id}/{MODEL} = {row_ab} (absent or wrong shape after 90s)")
    ratios_rb = json.loads(db_one("SELECT value FROM options WHERE key = 'ModelRatio'")[0])
    if ratios_rb.get(MODEL) != 0:
        raise RuntimeError(f"ModelRatio[{MODEL}] readback = {ratios_rb.get(MODEL)!r}")
    print(f"verify ok: abilities ch{new_id} (default,1,{PRIORITY},{WEIGHT}), ratio 0")

    # 4. functional: gateway chat + log attribution
    gateway_key = read_gateway_key()
    mark = time.time()
    status, body = smoke.http_json(
        f"{GATEWAY_BASE}/v1/chat/completions",
        method="POST",
        body={
            "model": MODEL,
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": 512,
        },
        headers={"Authorization": f"Bearer {gateway_key}"},
        timeout=180,
    )
    if status != 200:
        raise RuntimeError(f"gateway chat probe failed: HTTP {status}: {json.dumps(body)[:200]}")
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as c:
        row_log = c.execute(
            "SELECT channel_id FROM logs WHERE model_name = ? AND created_at > ? AND type = 2 "
            "ORDER BY id DESC LIMIT 1",
            (MODEL, mark),
        ).fetchone()
    attr = int(row_log[0]) if row_log else None
    if attr != new_id:
        raise RuntimeError(f"attribution mismatch: logs ch{attr}, expected ch{new_id}")
    usage = body.get("usage") or {}
    print(
        f"functional OK: gateway chat 200 usage={usage.get('prompt_tokens')}/{usage.get('completion_tokens')} "
        f"attributed ch{attr}"
    )
    print(f"DONE: ch{new_id} {CHANNEL_NAME} live; free window ends 2026-10-31 (re-verify before November)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
