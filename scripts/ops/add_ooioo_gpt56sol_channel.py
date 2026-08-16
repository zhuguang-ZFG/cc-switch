#!/usr/bin/env python3
"""One-shot helper: create NewAPI channel for gpt-5.6-sol via the ooioo
gateway (https://ooioo.work) as a low-priority backup.

ooioo is a plain OpenAI-compatible gateway (/v1/chat/completions), so no
relay is needed — this is a standard type=1 channel. Priority 30 places it
below the active pair (muyuan-sol ch83 prio 50, agentrouter ch45 prio 40),
making it a second-line backup for gpt-5.6-sol.

Follows the ch83/ch84/ch85 workflow contract:
- dup check by name and (base_url, models) before creating
- whole-DB SQLite snapshot backup before any change
- POST /api/channel/ with {"mode":"single","channel":payload} double wrap
- channel + abilities readback verification after apply
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHANNEL_NAME = "ooioo-gpt-5.6-sol"
BASE_URL = "https://ooioo.work"  # NewAPI appends /v1/chat/completions itself
MODELS = "gpt-5.6-sol,zg-gpt-5.6-sol,zg-agent-gpt-5.6-sol"
MODEL_MAPPING = json.dumps(
    {"zg-gpt-5.6-sol": "gpt-5.6-sol", "zg-agent-gpt-5.6-sol": "gpt-5.6-sol"}
)
# Backup tier: below muyuan-sol (50) and agentrouter (40).
PRIORITY = 30
WEIGHT = 5


def mask(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]} (len={len(key)})"


def main() -> int:
    key = sys.argv[1] if len(sys.argv) > 1 else ""
    if not key:
        print("FATAL: pass the ooioo API key as argv[1] (kept out of the repo)")
        return 2

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    # 1. list channels, dup check
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=200", headers=headers
    )
    if status != 200 or not isinstance(body, dict):
        print(f"FATAL: channel list failed HTTP {status}")
        return 1
    items = body.get("data") or []
    if isinstance(items, dict):
        items = items.get("items") or []
    existing_id = None
    for ch in items:
        if not isinstance(ch, dict):
            continue
        if ch.get("name") == CHANNEL_NAME:
            existing_id = ch.get("id")
            break
        if ch.get("base_url") == BASE_URL and ch.get("models") == MODELS:
            print(
                f"REFUSE: equivalent channel exists (id={ch.get('id')} "
                f"name={ch.get('name')!r} base_url={BASE_URL} models={MODELS})"
            )
            return 3
    if existing_id is None:
        print(f"dup-check ok: {len(items)} channels, no name/base+models collision")
    else:
        print(f"channel {CHANNEL_NAME!r} already exists (id={existing_id}), verifying only")

    if existing_id is None:
        # 2. DB snapshot backup
        backup_dir = Path(smoke.NEWAPI_DB).parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dst = backup_dir / f"new-api-before-{CHANNEL_NAME}-{stamp}.db"
        src = sqlite3.connect(f"file:{Path(smoke.NEWAPI_DB).as_posix()}?mode=ro", uri=True, timeout=30)
        try:
            out = sqlite3.connect(str(dst), timeout=30)
            try:
                src.backup(out)
            finally:
                out.close()
        finally:
            src.close()
        print(f"backup ok: {dst.name} ({dst.stat().st_size} bytes)")

    if existing_id is None:
        # 3. create channel (double-wrapped body per fork contract)
        payload = {
            "mode": "single",
            "channel": {
                "name": CHANNEL_NAME,
                "type": 1,  # OpenAI (/v1/chat/completions)
                "key": key,
                "base_url": BASE_URL,
                "models": MODELS,
                "group": "default",
                "model_mapping": MODEL_MAPPING,
                "priority": PRIORITY,
                "weight": WEIGHT,
                "status": 1,
            },
        }
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/", method="POST", body=payload, headers=headers
        )
        if status != 200 or not isinstance(body, dict) or not body.get("success"):
            msg = body.get("message") if isinstance(body, dict) else body
            print(f"FATAL: create failed HTTP {status}: {msg}")
            return 1
        print("create accepted")

    # 4. readback: channel row
    new_id = None
    readback = None
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=200", headers=headers
    )
    rb_items = body.get("data") or []
    if isinstance(rb_items, dict):
        rb_items = rb_items.get("items") or []
    for ch in rb_items:
        if not isinstance(ch, dict):
            continue
        if ch.get("name") == CHANNEL_NAME:
            new_id = ch.get("id")
            readback = ch
            break
    if new_id is None:
        print("FATAL: created channel not found on readback")
        return 1
    ok = (
        readback.get("base_url") == BASE_URL
        and readback.get("models") == MODELS
        and readback.get("type") == 1
        and readback.get("status") == 1
        and readback.get("priority") == PRIORITY
    )
    print(
        f"readback channel id={new_id} status={readback.get('status')} "
        f"type={readback.get('type')} base_url={readback.get('base_url')} "
        f"priority={readback.get('priority')} models={readback.get('models')} "
        f"key={mask(str(readback.get('key') or key))}"
    )

    # 5. readback: abilities rows for the models
    con = sqlite3.connect(f"file:{Path(smoke.NEWAPI_DB).as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        rows = list(
            con.execute(
                "SELECT model, channel_id, enabled, priority, weight FROM abilities "
                "WHERE channel_id = ?",
                (new_id,),
            )
        )
    finally:
        con.close()
    ab_ok = any(r[0] == "gpt-5.6-sol" and r[2] for r in rows)
    print(f"abilities rows for ch{new_id}: {[(r[0], r[1], 'enabled' if r[2] else 'disabled', r[3], r[4]) for r in rows]}")

    if not (ok and ab_ok):
        print("VERIFY FAILED: channel or abilities readback mismatch")
        return 1
    print(f"OK: ch{new_id} {CHANNEL_NAME} live, gpt-5.6-sol enabled in abilities at priority {PRIORITY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
