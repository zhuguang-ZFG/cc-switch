#!/usr/bin/env python3
"""One-shot helper: create a NewAPI channel for the ClinePass subscription
(api.cline.bot, key expires 2026-10-18).

ClinePass semantics (verified 2026-10-10 by direct probes):
- The subscription key authenticates at https://api.cline.bot/api/v1 but only
  `cline-pass/<model>` ids draw from the subscription. Any other model id
  (including the 458-entry billing catalog and even `:free` variants) is
  charged Cline Credits and hard-fails 402 `insufficient_credits` at $0.01.
- Usage is capped by a 5-hour rolling window plus weekly/monthly caps;
  exhaustion returns 429 `INFERENCE_CAP_ERROR`, which does not match the
  Guardian hard-error keywords (402/401/502/invalid) and degrades gracefully.
- Subscription catalog on 2026-10-10 (docs.cline.bot/getting-started/clinepass):
  deepseek-v4-pro, deepseek-v4.1-flash, glm-5.3, glm-5.3-flash, kimi-k3,
  mimo-v2.5, mimo-v2.5-pro, minimax-m3, muse-spark-1.3-contributor,
  qwen3.7-max, qwen3.7-plus, qwen3.8-max.

Exposed model names are the pool-canonical short names (glm-5.3, kimi-k3, ...)
mapped to `cline-pass/*` upstream, so this channel joins the existing
aggregation pools for those models. priority 30 / weight 3 keeps it a modest
pool member: the rolling-window 429s must not stampede the pool.

This is distinct from ch195/ch196 (older usage-billing cline.bot keys mapped to
`vendor/model` ids); those channels are not modified here.

Workflow contract (ch83/ch84/ch85/ch87/ch90 lineage):
- dup check by name and (base_url, models) before creating
- whole-DB SQLite snapshot backup before any change
- single key: POST /api/channel/ directly; multi key: mode=multi_to_single
  plus channel_info DB remediation (never PUT after that fix)
- channel + abilities readback verification after apply
- the key is passed via argv only and never written to the repo
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")

CHANNEL_NAME = "clinepass"
BASE_URL = "https://api.cline.bot/api"  # NewAPI appends /v1/chat/completions.
MODEL_MAP = {
    "deepseek-v4-pro": "cline-pass/deepseek-v4-pro",
    "deepseek-v4.1-flash": "cline-pass/deepseek-v4.1-flash",
    "glm-5.3": "cline-pass/glm-5.3",
    "glm-5.3-flash": "cline-pass/glm-5.3-flash",
    "kimi-k3": "cline-pass/kimi-k3",
    "mimo-v2.5": "cline-pass/mimo-v2.5",
    "mimo-v2.5-pro": "cline-pass/mimo-v2.5-pro",
    "minimax-m3": "cline-pass/minimax-m3",
    "muse-spark-1.3-contributor": "cline-pass/muse-spark-1.3-contributor",
    "qwen3.7-max": "cline-pass/qwen3.7-max",
    "qwen3.7-plus": "cline-pass/qwen3.7-plus",
    "qwen3.8-max": "cline-pass/qwen3.8-max",
}
MODELS = ",".join(MODEL_MAP)
MODEL_MAPPING = json.dumps(MODEL_MAP, ensure_ascii=False)
GROUP = "default"
PRIORITY = 30
WEIGHT = 3
TEST_MODEL = "glm-5.3-flash"


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


def main() -> int:
    keys = [k.strip() for k in sys.argv[1:] if k.strip()]
    if not keys:
        print("FATAL: pass one or more ClinePass API keys as argv (kept out of the repo)")
        return 2
    key_field = "\n".join(keys) if len(keys) > 1 else keys[0]

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

    needs_create = True
    if existing_id is not None:
        # Idempotent re-run: verify-only unless key count changed.
        con = sqlite3.connect(
            f"file:{Path(smoke.NEWAPI_DB).as_posix()}?mode=ro", uri=True, timeout=30
        )
        try:
            row = con.execute(
                "SELECT CAST(channel_info AS TEXT), CAST(key AS TEXT) FROM channels WHERE id = ?",
                (existing_id,),
            ).fetchone()
        finally:
            con.close()
        try:
            info = json.loads(row[0]) if row and row[0] else {}
        except (json.JSONDecodeError, TypeError):
            info = {}
        stored_key = row[1] if row else ""
        stored_lines = (stored_key or "").count("\n") + 1 if stored_key else 0
        same_keys = len(keys) == 1 and stored_key == keys[0]
        multi_ok = (
            len(keys) > 1
            and info.get("is_multi_key")
            and info.get("multi_key_size") == len(keys)
        )
        if same_keys or multi_ok:
            print(f"channel {CHANNEL_NAME!r} already exists (id={existing_id}), verifying only")
            needs_create = False
        else:
            print(
                f"REFUSE: channel {CHANNEL_NAME!r} exists (id={existing_id}) with "
                f"different keys/posture (stored_lines={stored_lines}, info={info!r}); "
                "refusing blind delete — reconcile manually"
            )
            return 3

    if needs_create:
        # 2. DB snapshot backup
        backup_dir = Path(smoke.NEWAPI_DB).parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dst = backup_dir / f"new-api-before-{CHANNEL_NAME}-{stamp}.db"
        if dst.exists():
            print(f"FATAL: backup destination already exists: {dst} (refusing to overwrite)")
            return 1
        src = sqlite3.connect(
            f"file:{Path(smoke.NEWAPI_DB).as_posix()}?mode=ro", uri=True, timeout=30
        )
        try:
            out = sqlite3.connect(str(dst), timeout=30)
            try:
                src.backup(out)
            finally:
                out.close()
        finally:
            src.close()
        print(f"backup ok: {dst.name} ({dst.stat().st_size} bytes)")

    channel_body = {
        "name": CHANNEL_NAME,
        "type": 1,  # OpenAI-compatible /v1/chat/completions
        "key": key_field,
        "base_url": BASE_URL,
        "models": MODELS,
        "group": GROUP,
        "model_mapping": MODEL_MAPPING,
        "test_model": TEST_MODEL,
        "priority": PRIORITY,
        "weight": WEIGHT,
        "status": 1,
        "auto_ban": 1,
    }
    if needs_create:
        # 3. create channel
        # This fork's POST /api/channel/ always expects the wrapped shape;
        # mode=single stores the key verbatim (fine for one key), and
        # multi_to_single is the only create path that sets is_multi_key
        # (docs/ops/t1qq-sol-channel-2026-08-16.md trap list).
        if len(keys) > 1:
            payload = {"mode": "multi_to_single", "channel": channel_body}
        else:
            payload = {"mode": "single", "channel": channel_body}
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/", method="POST", body=payload, headers=headers
        )
        if status != 200 or not isinstance(body, dict) or not body.get("success"):
            msg = body.get("message") if isinstance(body, dict) else body
            print(f"FATAL: create failed HTTP {status}: {msg}")
            return 1
        print(f"create accepted ({len(keys)} key(s))")

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
        if isinstance(ch, dict) and ch.get("name") == CHANNEL_NAME:
            new_id = ch.get("id")
            readback = ch
            break
    if new_id is None:
        print("FATAL: channel not found on readback")
        return 1

    # 4b. multi-key remediation (only for freshly created multi-key channels).
    needs_fix = False
    if needs_create and len(keys) > 1:
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
            print("waiting 75s for SyncChannelCache to pick up channel_info...")
            time.sleep(75)

    expected = {
        "base_url": BASE_URL,
        "models": MODELS,
        "type": 1,
        "status": 1,
        "priority": PRIORITY,
        "weight": WEIGHT,
        "group": GROUP,
        "auto_ban": 1,
        "test_model": TEST_MODEL,
    }
    mismatch = {k: (readback.get(k), v) for k, v in expected.items() if readback.get(k) != v}
    try:
        mapping_match = json.loads(readback.get("model_mapping") or "null") == MODEL_MAP
    except json.JSONDecodeError:
        mapping_match = False
    print(
        f"readback channel id={new_id} status={readback.get('status')} "
        f"type={readback.get('type')} base_url={readback.get('base_url')} "
        f"priority={readback.get('priority')} weight={readback.get('weight')} "
        f"group={readback.get('group')!r}"
    )
    if mismatch or not mapping_match:
        print(f"mismatch={mismatch} mapping_match={mapping_match}")

    # 5. readback: abilities rows for the models
    con = sqlite3.connect(
        f"file:{Path(smoke.NEWAPI_DB).as_posix()}?mode=ro", uri=True, timeout=30
    )
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
    got = {r[0]: (r[1], r[2], r[3], r[4]) for r in rows}
    ab_ok = len(rows) == len(expected_models) and all(
        m in got and got[m][0] == new_id and got[m][1] and got[m][2] == PRIORITY and got[m][3] == WEIGHT
        for m in expected_models
    )
    print(f"abilities rows for ch{new_id}: {[(r[0], r[1], 'enabled' if r[2] else 'disabled', r[3], r[4]) for r in rows]}")

    ok = (not mismatch) and mapping_match
    if not (ok and ab_ok):
        print("VERIFY FAILED: channel or abilities readback mismatch")
        return 1
    print(
        f"OK: ch{new_id} {CHANNEL_NAME} live ({len(keys)} key(s)), ClinePass pool "
        f"member at priority {PRIORITY} weight {WEIGHT} (expires 2026-10-18)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
