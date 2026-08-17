#!/usr/bin/env python3
"""One-shot helper: create NewAPI channel for gpt-5.6-sol via the jianzhile
gateway (https://jianzhile.vip) as the direct backup to zzzcoding.

jianzhile.vip is a NewAPI fork relay exposing exactly one model
(`gpt-5.6-sol`) through Codex-shaped `/v1/responses`. NewAPI's channel-local
Chat-to-Responses policy lets OMP keep using its standard Chat Completions
provider while type=1 ch91 talks Responses upstream. Its priority 55 / weight 5
posture keeps it directly behind the zzzcoding primary at priority 60.

History: this gateway deterministically 403'd on 2026-08-13 (see
docs/ops/jianzhile-channel-2026-08-13.md) and was refused then; the first
key passed the upstream admission probe on 2026-08-17 and went in as
single-key ch91. A second user-supplied key arrived the same day and both
keys passed the probe, so the channel was converted to multi-key polling
(delete + multi_to_single recreate — is_multi_key is creation-time only on
this fork; see docs/ops/t1qq-sol-channel-2026-08-16.md for the trap list).

Follows the ch83/ch84/ch85/ch87/ch90 workflow contract:
- dup check by name and (base_url, models) before creating
- delete+recreate only when the same-name channel is not multi-key
- whole-DB SQLite snapshot backup before any change
- POST /api/channel/ with {"mode":"multi_to_single","channel":payload}
- channel_info DB remediation (full BLOB write, then wait for
  SyncChannelCache — never PUT afterwards, PUT clobbers the fix)
- channel + abilities readback verification after apply
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path

from fix_jianzhile_codex_channel import (
    CODEX_MODEL,
    HEADER_OVERRIDE,
    PARAM_OVERRIDE,
)

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHANNEL_NAME = "jianzhile-gpt-5.6-sol"
BASE_URL = "https://jianzhile.vip"  # NewAPI appends the request-specific API path.
MODELS = f"gpt-5.6-sol,zg-gpt-5.6-sol,zg-agent-gpt-5.6-sol,{CODEX_MODEL}"
MODEL_MAPPING = json.dumps(
    {
        "zg-gpt-5.6-sol": "gpt-5.6-sol",
        "zg-agent-gpt-5.6-sol": "gpt-5.6-sol",
        CODEX_MODEL: "gpt-5.6-sol",
    }
)
HEADER_OVERRIDE_JSON = json.dumps(HEADER_OVERRIDE, separators=(",", ":"), sort_keys=True)
PARAM_OVERRIDE_JSON = json.dumps(PARAM_OVERRIDE, separators=(",", ":"), sort_keys=True)
# User-selected direct backup tier, immediately below zzzcoding.
PRIORITY = 55
WEIGHT = 5

# Fork multi-key contract (docs/ops/tabitoken-channel-2026-08-09.md,
# extended by docs/ops/t1qq-sol-channel-2026-08-16.md):
# - mode MUST be "multi_to_single"; only that path sets is_multi_key=true.
#   A "single"-mode create stores the newline-joined key verbatim and every
#   request dies with `net/http: invalid header field value for
#   "Authorization"`.
# - Even multi_to_single may leave channel_info at
#   {"is_multi_key":false,"multi_key_status_list":null,...}; fix by
#   DB-writing the full ch75-shaped BLOB and waiting for SyncChannelCache.
# - Do NOT PUT after the DB fix: PUT regenerates channel_info from the
#   request body and clobbers it.


def mask(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]} (len={len(key)})"


def main() -> int:
    keys = [k.strip() for k in sys.argv[1:] if k.strip()]
    if not keys:
        print("FATAL: pass one or more jianzhile API keys as argv (kept out of the repo)")
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
        # creation-time only on this fork — a "single"-mode channel can never
        # be fixed in place). A healthy multi-key channel falls through to
        # readback verification untouched.
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
        if info.get("is_multi_key") and info.get("multi_key_size") == len(keys):
            print(f"channel {CHANNEL_NAME!r} already exists (id={existing_id}, multi-key ok), verifying only")
        else:
            print(f"channel {CHANNEL_NAME!r} exists (id={existing_id}) but multi-key mismatch — deleting for multi-key recreate")
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
                "type": 1,  # OpenAI-compatible; real Codex uses /v1/responses.
                "key": key_field,
                "base_url": BASE_URL,
                "models": MODELS,
                "group": "default",
                "model_mapping": MODEL_MAPPING,
                "header_override": HEADER_OVERRIDE_JSON,
                "param_override": PARAM_OVERRIDE_JSON,
                "test_model": CODEX_MODEL,
                "priority": PRIORITY,
                "weight": WEIGHT,
                "status": 1,
                "auto_ban": 1,
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
    # Observed on this fork build (2026-08-16, ch90): even
    # mode="multi_to_single" can leave channel_info at
    # {"is_multi_key":false,"multi_key_size":0,"multi_key_status_list":null},
    # and a follow-up PUT REGENERATES channel_info from the request body
    # (clobbering partial DB fixes). Working recipe: DB-write the full
    # ch75-shaped channel_info BLOB and let SyncChannelCache (60s) pick it
    # up — no PUT afterwards.
    TARGET_INFO = {
        "is_multi_key": True,
        "multi_key_size": len(keys),
        "multi_key_status_list": {},
        "multi_key_polling_index": 0,
        "multi_key_mode": "polling",
    }
    needs_fix = False
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

    expected = {
        "base_url": BASE_URL,
        "models": MODELS,
        "type": 1,
        "status": 1,
        "priority": PRIORITY,
        "weight": WEIGHT,
        "group": "default",
        "auto_ban": 1,
        "test_model": CODEX_MODEL,
    }
    mismatch = {k: (readback.get(k), v) for k, v in expected.items() if readback.get(k) != v}
    try:
        mapping_match = json.loads(readback.get("model_mapping") or "null") == json.loads(MODEL_MAPPING)
    except json.JSONDecodeError:
        mapping_match = False
    try:
        header_match = json.loads(readback.get("header_override") or "null") == HEADER_OVERRIDE
    except json.JSONDecodeError:
        header_match = False
    try:
        param_match = json.loads(readback.get("param_override") or "null") == PARAM_OVERRIDE
    except json.JSONDecodeError:
        param_match = False
    ok = (not mismatch) and mapping_match and header_match and param_match
    rb_key = str(readback.get("key") or "")
    print(
        f"readback channel id={new_id} status={readback.get('status')} "
        f"type={readback.get('type')} base_url={readback.get('base_url')} "
        f"priority={readback.get('priority')} models={readback.get('models')} "
        f"key_lines={rb_key.count(chr(10)) + 1 if rb_key else 0} (api redacts keys)"
    )
    if mismatch or not mapping_match or not header_match or not param_match:
        print(
            f"mismatch={mismatch} mapping_match={mapping_match} "
            f"header_match={header_match} param_match={param_match}"
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
    print(f"OK: ch{new_id} {CHANNEL_NAME} live ({len(keys)} keys, multi-key polling), "
          f"Sol backup channel at priority {PRIORITY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
