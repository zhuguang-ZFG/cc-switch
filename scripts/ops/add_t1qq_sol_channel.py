#!/usr/bin/env python3
"""One-shot helper: create NewAPI channel for the t1qq gateway
(https://ai.t1qq.com) as the last-resort (兜底) gpt-5.6-sol backup.

Two keys supplied by the user; created as a multi-key channel (newline-
separated key field).

Timeline (2026-08-16): gateway hard-down ~23:38–23:47 (every completion,
both keys, all models -> instant {"error":{"message":"Service temporarily
unavailable"}} while /v1/models kept listing); recovered by ~23:49 — first
channel test after the multi-key fix passed (2.3s) and polling advanced.

Fork multi-key traps hit and fixed here (see docs/ops/tabitoken-channel-
2026-08-09.md for the original contract):
- "single"-mode create stores the newline-joined key verbatim ->
  `net/http: invalid header field value for "Authorization"` on every request.
- mode "multi_to_single" alone did NOT set channel_info.is_multi_key on this
  fork build; a follow-up PUT rewrote channel_info from the request body and
  clobbered a partial DB fix. Final fix: DB-write the full ch75-shaped
  channel_info BLOB (is_multi_key=true, multi_key_size=2, status_list={},
  polling_index=0, mode=polling) and let the 60s SyncChannelCache pick it up.
  Verified: IsMultiKey:true in server log, polling_index 0->1 across tests.

Priority ladder after this change:
  ch83 muyuan-sol 50 (primary, degraded — see sol-chain-muyuan-degradation)
  ch45 agentrouter 40
  ch87 ooioo       30
  ch90 t1qq        20 (this channel, last resort)

Follows the ch83/84/85/87/88/89 workflow contract:
- dup check by name and (base_url, models) before creating; a same-name
  channel is DELETED and recreated (a "single"-mode multi-key channel can
  never be fixed in place — is_multi_key is creation-time only)
- whole-DB SQLite snapshot backup before any change (refuses overwrite)
- POST /api/channel/ with {"mode":"multi_to_single","channel":payload}
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


CHANNEL_NAME = "t1qq-gpt-5.6-sol"
BASE_URL = "https://ai.t1qq.com"  # NewAPI appends /v1/chat/completions itself
MODELS = "gpt-5.6-sol,zg-gpt-5.6-sol,zg-agent-gpt-5.6-sol"
MODEL_MAPPING = json.dumps(
    {"zg-gpt-5.6-sol": "gpt-5.6-sol", "zg-agent-gpt-5.6-sol": "gpt-5.6-sol"}
)
# Last-resort tier: below ooioo (30).
PRIORITY = 20
WEIGHT = 5

# Fork multi-key contract (docs/ops/tabitoken-channel-2026-08-09.md):
# - mode MUST be "multi_to_single"; only that path sets is_multi_key=true.
#   A "single"-mode create stores the newline-joined key verbatim and every
#   request dies with `net/http: invalid header field value for
#   "Authorization"` (observed on ch90 before this fix).
# - API-created multi-key channels persist channel_info.multi_key_status_list
#   as null, which freezes polling on key1 forever. Fix: DB write
#   channel_info=CAST('{}' AS BLOB), then a PUT to refresh the cache.
# - multi_key_mode is not persisted at creation; a follow-up PUT must carry
#   top-level multi_key_mode="polling". PUT body must NOT contain `status`
#   (else "Invalid parameters") and MUST re-supply the real key (GET redacts
#   it; round-tripping the redacted "" wipes the stored keys).


def mask(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]} (len={len(key)})"


def main() -> int:
    keys = [k.strip() for k in sys.argv[1:] if k.strip()]
    if not keys:
        print("FATAL: pass one or more t1qq API keys as argv (kept out of the repo)")
        return 2
    key_field = "\n".join(keys)  # multi-key channel

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
        # A same-name channel exists. Only delete+recreate when its
        # channel_info shows multi-key was never enabled (is_multi_key is
        # creation-time only on this fork — a "single"-mode multi-key channel
        # can never be fixed in place). A healthy multi-key channel falls
        # through to readback verification untouched.
        con = sqlite3.connect(f"file:{Path(smoke.NEWAPI_DB).as_posix()}?mode=ro", uri=True, timeout=30)
        try:
            row = con.execute(
                "SELECT CAST(channel_info AS TEXT) FROM channels WHERE id = ?", (existing_id,)
            ).fetchone()
        finally:
            con.close()
        try:
            info = json.loads(row[0]) if row and row[0] else {}
        except json.JSONDecodeError:
            info = {}
        if info.get("is_multi_key"):
            print(f"channel {CHANNEL_NAME!r} already exists (id={existing_id}, multi-key ok), verifying only")
        else:
            print(f"channel {CHANNEL_NAME!r} exists (id={existing_id}) but is_multi_key=false — deleting for multi-key recreate")
            status, body = smoke.http_json(
                f"{smoke.NEWAPI_BASE}/api/channel/{existing_id}", method="DELETE", headers=headers
            )
            if status != 200 or not isinstance(body, dict) or not body.get("success"):
                msg = body.get("message") if isinstance(body, dict) else body
                print(f"FATAL: delete of ch{existing_id} failed HTTP {status}: {msg}")
                return 1
            print(f"deleted ch{existing_id}")
            existing_id = None

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
        # 3. create channel (multi_to_single: the only path that sets
        # is_multi_key=true on this fork)
        payload = {
            "mode": "multi_to_single",
            "channel": {
                "name": CHANNEL_NAME,
                "type": 1,  # OpenAI (/v1/chat/completions)
                "key": key_field,
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
        print(f"create accepted ({len(keys)} keys, multi-key)")

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

    # 4b. multi-key remediation (only needed right after creation).
    # Observed on this fork build (2026-08-16): even mode="multi_to_single"
    # leaves channel_info at {"is_multi_key":false,"multi_key_size":0,
    # "multi_key_status_list":null,...}, and a follow-up PUT REGENERATES
    # channel_info from the request body (clobbering partial DB fixes).
    # Working recipe: DB-write the full ch75-shaped channel_info BLOB and let
    # SyncChannelCache (60s) pick it up — no PUT afterwards.
    TARGET_INFO = {
        "is_multi_key": True,
        "multi_key_size": len(keys),
        "multi_key_status_list": {},
        "multi_key_polling_index": 0,
        "multi_key_mode": "polling",
    }
    con = sqlite3.connect(str(Path(smoke.NEWAPI_DB)), timeout=30)
    try:
        row = con.execute(
            "SELECT CAST(channel_info AS TEXT) FROM channels WHERE id = ?", (new_id,)
        ).fetchone()
        info_text = (row[0] if row and row[0] else "") or ""
        try:
            info = json.loads(info_text) if info_text.strip() else {}
        except json.JSONDecodeError:
            info = {}
        print(f"channel_info readback: {info!r}")
        needs_fix = not (
            info.get("is_multi_key")
            and info.get("multi_key_size") == len(keys)
            and isinstance(info.get("multi_key_status_list"), dict)
            and info.get("multi_key_mode") == "polling"
        )
        if needs_fix:
            con.execute(
                "UPDATE channels SET channel_info = CAST(? AS BLOB) WHERE id = ?",
                (json.dumps(TARGET_INFO), new_id),
            )
            con.commit()
            print(f"channel_info rewritten: {TARGET_INFO!r}")
    finally:
        con.close()
    if needs_fix:
        # The in-memory channel cache refreshes every SyncFrequency (60s);
        # only then does the relay see is_multi_key=true.
        print("waiting 75s for SyncChannelCache to pick up channel_info...")
        time.sleep(75)
        t0 = time.time()
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/test/{new_id}?model=gpt-5.6-sol",
            headers=headers,
        )
        # Informational only: with a healthy upstream this must succeed; with
        # a down upstream it fails either way — the config proof is the
        # channel_info readback above.
        print(
            f"channel test after cache sync: {time.time()-t0:.1f}s -> "
            f"{json.dumps(body, ensure_ascii=False)[:160] if isinstance(body, dict) else body}"
        )
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
    ok = (not mismatch) and mapping_match
    rb_key = str(readback.get("key") or "")
    print(
        f"readback channel id={new_id} status={readback.get('status')} "
        f"type={readback.get('type')} base_url={readback.get('base_url')} "
        f"priority={readback.get('priority')} models={readback.get('models')} "
        f"key_lines={rb_key.count(chr(10)) + 1 if rb_key else 0} (api redacts keys)"
    )
    if mismatch or not mapping_match:
        print(f"mismatch={mismatch} mapping_match={mapping_match}")

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
    print(f"OK: ch{new_id} {CHANNEL_NAME} registered ({len(keys)} keys, "
          f"multi-key polling); last-resort sol backup at priority {PRIORITY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
