#!/usr/bin/env python3
"""One-shot helper: create NewAPI channel for the seeseed1ck hydrogel gateway
(https://api-yi-hydrogel.seeseed1ck.icu) covering only the models verified live
on 2026-08-16.

Gateway liveness sweep (minimal + full OMP wire shape, direct, 2026-08-16):
- live: GLM-5.3, grok-4.6, grok-chat-fast, mimo-v2.5
- excluded: GLM-5.2 (429 concurrent limit saturated, running=6 max=6),
  deepseek-v4-pro/flash, glm-5.2 (lowercase), kimi-k2.6, qwen3.7/3.8-* (502),
  grok-4.5, gpt-oss-120b (upstream do_request_failed), longcat-2.0-free
  (no upstream channel in default group)

Two gateway quirks handled by param_override:
- GLM-5.3 is always-thinking and 400s on enable_thinking:false ("该模型始终
  思考，不支持关闭思考"). OMP sends enable_thinking in its wire shape, so the
  channel deletes the field for every model (server defaults then apply;
  grok/mimo accept the field but their defaults are fine).
- prompt_cache_key is tolerated (GLM-5.3/grok-4.6 streamed 200 with it), so no
  delete op is needed for it.

Follows the ch83/ch84/ch85/ch87/ch88 workflow contract:
- dup check by name and (base_url, models) before creating
- whole-DB SQLite snapshot backup before any change (refuses overwrite)
- POST /api/channel/ with {"mode":"single","channel":payload} double wrap
- channel + abilities + param_override readback verification after apply
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


CHANNEL_NAME = "seeseed1ck-hydrogel"
BASE_URL = "https://api-yi-hydrogel.seeseed1ck.icu"  # NewAPI appends /v1/... itself
MODELS = "GLM-5.3,grok-4.6,grok-chat-fast,mimo-v2.5"
MODEL_MAPPING = "{}"
PARAM_OVERRIDE = json.dumps(
    {"operations": [{"path": "enable_thinking", "mode": "delete"}]}
)
# No existing channel serves these models; priority only matters for future
# same-model additions. 0 = neutral (same as ch88).
PRIORITY = 0
WEIGHT = 0


def mask(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]} (len={len(key)})"


def main() -> int:
    key = sys.argv[1] if len(sys.argv) > 1 else ""
    if not key:
        print("FATAL: pass the hydrogel API key as argv[1] (kept out of the repo)")
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
        if dst.exists():
            print(f"FATAL: backup destination already exists: {dst} (refusing to overwrite)")
            return 1
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
                "param_override": PARAM_OVERRIDE,
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
    if status != 200 or not isinstance(body, dict):
        print(f"FATAL: readback list failed HTTP {status}")
        return 1
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
    expected = {
        "base_url": BASE_URL,
        "models": MODELS,
        "type": 1,
        "status": 1,
        "priority": PRIORITY,
        "weight": WEIGHT,
        "group": "default",
    }
    mismatch = {k: (readback.get(k), v) for k, v in expected.items() if readback.get(k) != v}
    try:
        mapping_match = json.loads(readback.get("model_mapping") or "null") == json.loads(MODEL_MAPPING)
    except json.JSONDecodeError:
        mapping_match = False
    try:
        override_match = json.loads(readback.get("param_override") or "null") == json.loads(PARAM_OVERRIDE)
    except json.JSONDecodeError:
        override_match = False
    ok = (not mismatch) and mapping_match and override_match
    print(
        f"readback channel id={new_id} status={readback.get('status')} "
        f"type={readback.get('type')} base_url={readback.get('base_url')} "
        f"priority={readback.get('priority')} models={readback.get('models')} "
        f"key={mask(str(readback.get('key') or key))}"
    )
    if mismatch or not mapping_match or not override_match:
        print(f"mismatch={mismatch} mapping_match={mapping_match} override_match={override_match}")

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
    expected_models = MODELS.split(",")
    got = {r[0]: (r[1], r[2], r[3], r[4]) for r in rows}  # model -> (channel_id, enabled, priority, weight)
    ab_ok = len(rows) == len(expected_models) and all(
        m in got and got[m][0] == new_id and got[m][1] and got[m][2] == PRIORITY and got[m][3] == WEIGHT
        for m in expected_models
    )
    print(f"abilities rows for ch{new_id}: {[(r[0], r[1], 'enabled' if r[2] else 'disabled', r[3], r[4]) for r in rows]}")

    if not (ok and ab_ok):
        print("VERIFY FAILED: channel or abilities readback mismatch")
        return 1
    print(f"OK: ch{new_id} {CHANNEL_NAME} live, {len(expected_models)} models enabled in abilities")
    return 0


if __name__ == "__main__":
    sys.exit(main())
