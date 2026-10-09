#!/usr/bin/env python3
"""Onboard muyuan.do (君の公益 shared pool, 25 models) as a backup-tier channel in local NewAPI (2026-10-06).

Precedent: add_obitmc_relay_channel.py / add_budsin_apichat_channel.py.
- Backup posture p20/w1 (glm-5.2 shares pool with enabled ch15 sensenova-token p50;
  the other 24 models have no enabled carrier, this channel is their only source).
- Pricing: read-only parity report, never overwrite ratios (budsin rejected-design #3).
- Idempotent resume with drift detection; DB online backup before any write.
- key via MUYUAN_KEY env only (never printed, never persisted).
- mistral-large-4-0 deliberately excluded: upstream 400 model_price_error (unpriced).
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
# base_url WITHOUT /v1: NewAPI auto-appends /v1/chat/completions
BASE_URL = "https://muyuan.do"
CHANNEL_NAME = "muyuan-gongyi"
MODELS = [
    "mistral-embed",
    "qwen3.8-27b",
    "ministral-14b-latest",
    "codestral-embed-2505",
    "mistral-code-fim-latest",
    "codestral-2508",
    "glm-5.3-200k",
    "codestral-latest",
    "glm-4.5-flash",
    "voxtral-small-2507",
    "voxtral-mini-2602",
    "ministral-3b-latest",
    "ministral-14b-2512",
    "glm-5.2-200k",
    "voxtral-small-latest",
    "ministral-8b-2512",
    "glm-5.2",
    "voxtral-mini-latest",
    "mistral-embed-2312",
    "qwen3.8-flash-next",
    "ministral-3b-2512",
    "grok-4.7",
    "ministral-8b-latest",
    "codestral-embed",
    "mistral-code-latest",
    # added 2026-10-06 ~23:15 UTC+8: upstream priced mistral-large-4 family
    # (first probe 21:55: 400 model_price_error and absent from /v1/models)
    "mistral-large-4-0",
    "mistral-large-4",
]
MODEL_MAPPING: dict[str, str] = {}  # upstream serves exact same ids
# cheapest proven-id model for admin channel test (probe: ministral-3b-latest 200 "Pong")
TEST_MODEL = "ministral-3b-latest"
PRIORITY = 20
WEIGHT = 1
# models with a pre-existing enabled primary; traffic must NOT migrate to the new channel
CONTESTED = {"glm-5.2": 15}  # ch15 sensenova-token p50/w1


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load smoke module from {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def db_one(sql, params=()):
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as c:
        return c.execute(sql, params).fetchone()


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = backup_dir / f"new-api-before-muyuan-gongyi-{stamp}.db"
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as src:
        with closing(sqlite3.connect(destination.as_posix())) as dst:
            src.backup(dst)
            integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"backup integrity_check={integrity}")
    return destination


def fetch_channels(smoke, headers):
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=500", headers=headers
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"channel list failed: HTTP {status}")
    items = (body.get("data") or {}).get("items") or []
    return [i for i in items if isinstance(i, dict)]


def read_gateway_key() -> str:
    """OMP zg-newapi apiKey from live models.yml (never printed)."""
    text = (Path.home() / ".omp" / "agent" / "models.yml").read_text(encoding="utf-8")
    import re

    match = re.search(r"zg-newapi.*?apiKey:\s*(\S+)", text, re.S)
    if not match:
        raise RuntimeError("zg-newapi apiKey not found in models.yml")
    return match.group(1)


def wait_abilities(channel_id: int, model: str, seconds: int = 90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        row = db_one(
            'SELECT "group", enabled, priority, weight FROM abilities WHERE channel_id = ? AND model = ?',
            (channel_id, model),
        )
        if row:
            return row
        time.sleep(3)
    return None


def gateway_chat(smoke, gateway_key: str, model: str, max_tokens: int = 64):
    """One gateway chat; returns (status, usage, attribution_channel_id)."""
    mark = time.time()
    status, body = smoke.http_json(
        f"{GATEWAY_BASE}/v1/chat/completions",
        method="POST",
        body={
            "model": model,
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": max_tokens,
        },
        headers={"Authorization": f"Bearer {gateway_key}"},
        timeout=180,
    )
    usage = (body or {}).get("usage") or {}
    attr = None
    for _ in range(6):
        row_log = db_one(
            "SELECT channel_id FROM logs WHERE model_name = ? AND created_at > ? AND type = 2 "
            "ORDER BY id DESC LIMIT 1",
            (model, mark),
        )
        if row_log:
            attr = int(row_log[0])
            break
        time.sleep(5)
    return status, usage, attr


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    parser.add_argument(
        "--extend",
        action="store_true",
        help="allow pure model-list additions to the existing channel (PUT with key re-supplied)",
    )
    args = parser.parse_args()
    extension_pending = False

    print("plan:")
    print(f"  create: {CHANNEL_NAME} models={len(MODELS)} prio={PRIORITY} w={WEIGHT} auto_ban=1")
    print(f"  base: {BASE_URL} (no /v1; NewAPI auto-appends)  mapping: none (exact ids)")
    print(f"  contested pools: {json.dumps(CONTESTED)} -> new channel must NOT win these")
    print("  pricing: read-only parity report (never overwrite ratios)")
    print("  POST /api/channel/fix (abilities rebuild)")
    print("  functional: admin channel test + gateway chats (contested stays primary; unique models serve)")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    key = os.environ.get("MUYUAN_KEY", "").strip()
    if not key:
        raise RuntimeError("MUYUAN_KEY env var required")

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    channels = fetch_channels(smoke, headers)
    by_name = {str(c.get("name")): c for c in channels}
    existing = by_name.get(CHANNEL_NAME)
    reused_id: int | None = None
    if existing is not None:
        mismatch = []
        if str(existing.get("base_url") or "") != BASE_URL:
            mismatch.append(f"base_url={existing.get('base_url')!r}")
        if str(existing.get("models") or "") != ",".join(MODELS):
            mismatch.append(f"models={existing.get('models')!r}")
        if int(existing.get("priority") or -1) != PRIORITY:
            mismatch.append(f"priority={existing.get('priority')!r}")
        if int(existing.get("weight") or -1) != WEIGHT:
            mismatch.append(f"weight={existing.get('weight')!r}")
        stored_raw = existing.get("model_mapping")
        stored_mapping_obj = stored_raw if isinstance(stored_raw, dict) else json.loads(str(stored_raw or "") or "{}")
        if stored_mapping_obj != MODEL_MAPPING:
            mismatch.append(f"model_mapping={stored_raw!r}")
        if mismatch:
            stored_models = set(str(existing.get("models") or "").split(",")) - {""}
            want_models = set(MODELS)
            pure_extension = stored_models < want_models and len(mismatch) == 1 and mismatch[0].startswith("models=")
            if not (args.extend and pure_extension):
                raise RuntimeError(
                    f"channel {CHANNEL_NAME} (ch{existing['id']}) exists with drifted config: {mismatch}; "
                    "pure model additions may pass --extend; anything else requires manual reconciliation "
                    "(do not silently overwrite)"
                )
            extension_pending = True
            print(f"extend: ch{existing['id']} {CHANNEL_NAME} models {len(stored_models)} -> {len(want_models)} "
                  f"(+{sorted(want_models - stored_models)})")
        reused_id = int(existing["id"])
        if not extension_pending:
            print(f"resume: reusing existing ch{reused_id} {CHANNEL_NAME} (config matches)")

    backup = online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    if existing is not None:
        if reused_id is None:
            raise RuntimeError("resume path without reused_id (unreachable)")
        new_id = reused_id
        if extension_pending:
            # PUT rebuilds abilities; README contract: no status field, key must be
            # re-supplied (GET redacts it) — we hold it in env.
            payload = {
                "id": new_id,
                "name": CHANNEL_NAME,
                "type": 1,
                "base_url": BASE_URL,
                "key": key,
                "models": ",".join(MODELS),
                "model_mapping": json.dumps(MODEL_MAPPING, ensure_ascii=False),
                "group": "default",
                "priority": PRIORITY,
                "weight": WEIGHT,
                "auto_ban": 1,
                "test_model": TEST_MODEL,
            }
            status, body = smoke.http_json(
                f"{smoke.NEWAPI_BASE}/api/channel/",
                method="PUT",
                body=payload,
                headers=headers,
            )
            if status != 200 or not isinstance(body, dict) or not body.get("success"):
                message = body.get("message") if isinstance(body, dict) else None
                raise RuntimeError(f"channel PUT failed: HTTP {status} message={message!r}")
            print(f"updated ch{new_id}: models -> {len(MODELS)}")
        else:
            print(f"using ch{new_id} (resumed; channel already created)")
    else:
        payload = {
            "name": CHANNEL_NAME,
            "type": 1,
            "base_url": BASE_URL,
            "key": key,
            "models": ",".join(MODELS),
            "model_mapping": json.dumps(MODEL_MAPPING, ensure_ascii=False),
            "group": "default",
            "priority": PRIORITY,
            "weight": WEIGHT,
            "auto_ban": 1,
            "test_model": TEST_MODEL,
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

    # read-back: stored model set must equal MODELS exactly (catches silent PUT drops)
    after = fetch_channels(smoke, headers)
    match = [c for c in after if int(c.get("id") or 0) == new_id]
    if not match:
        raise RuntimeError(f"ch{new_id} not visible after create/update")
    stored_final = set(str(match[0].get("models") or "").split(",")) - {""}
    if stored_final != set(MODELS):
        raise RuntimeError(f"ch{new_id} read-back models mismatch: delta={sorted(set(MODELS) ^ stored_final)}")
    print(f"read-back ok: ch{new_id} models={len(stored_final)}")

    # pricing: read-only parity report
    row = db_one("SELECT value FROM options WHERE key = 'ModelRatio'")
    mr = json.loads(row[0]) if row and row[0] else {}
    row = db_one("SELECT value FROM options WHERE key = 'CompletionRatio'")
    cr = json.loads(row[0]) if row and row[0] else {}
    for m in MODELS:
        if m in mr or m in cr:
            print(f"  pricing: {m} -> ModelRatio={mr.get(m)}/{cr.get(m)}")
        else:
            print(f"  pricing: {m} -> absent (gateway default, parity with glm-5.3 precedent)")

    # fix + verify abilities for all models
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/fix", method="POST", body={}, headers=headers
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"channel fix failed: HTTP {status} message={message!r}")
    for m in MODELS:
        row_ab = wait_abilities(new_id, m)
        if not row_ab or tuple(row_ab) != ("default", 1, PRIORITY, WEIGHT):
            raise RuntimeError(f"abilities ch{new_id}/{m} = {row_ab} (absent or wrong shape after 90s)")
    print(f"verify ok: abilities ch{new_id} x{len(MODELS)} (default,1,{PRIORITY},{WEIGHT})")

    # admin channel test: real upstream call through NewAPI channel machinery
    t_status, t_body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/test/{new_id}?model={TEST_MODEL}",
        headers=headers,
        timeout=120,
    )
    t_ok = isinstance(t_body, dict) and t_body.get("success")
    t_ms = t_body.get("time") if isinstance(t_body, dict) else None
    print(f"channel test: HTTP {t_status} success={t_ok} time={t_ms} model={TEST_MODEL}")
    if not t_ok:
        message = t_body.get("message") if isinstance(t_body, dict) else None
        raise RuntimeError(f"channel test failed: {message!r}")

    gateway_key = read_gateway_key()

    # contested model: glm-5.2 must stay on its existing primary (ch15)
    status, usage, attr = gateway_chat(smoke, gateway_key, "glm-5.2")
    if status != 200:
        raise RuntimeError(f"gateway glm-5.2 chat failed: HTTP {status}")
    if attr == new_id:
        raise RuntimeError("unexpected: p20 backup won glm-5.2 while primary ch15 enabled")
    print(f"chain-health glm-5.2: 200 usage={usage.get('prompt_tokens')}/{usage.get('completion_tokens')} attributed ch{attr} (existing primary, as designed)")

    # unique models: new channel is the only source; end-to-end must work and attribute here
    for probe in ("grok-4.7", "codestral-latest", "qwen3.8-27b", "mistral-large-4-0"):
        status, usage, attr = gateway_chat(smoke, gateway_key, probe)
        if status != 200:
            raise RuntimeError(f"gateway {probe} chat failed: HTTP {status}")
        if attr != new_id:
            raise RuntimeError(f"{probe} attributed ch{attr}, expected ch{new_id} (only carrier)")
        print(f"serves {probe}: 200 usage={usage.get('prompt_tokens')}/{usage.get('completion_tokens')} attributed ch{attr} (sole carrier, as designed)")

    sole = len(MODELS) - len(CONTESTED)
    print(f"DONE: ch{new_id} {CHANNEL_NAME} live; contested={len(CONTESTED)} backup, {sole} models sole-carrier")
    print("NEXT: run scripts/ops/newapi-local-smoke.py (policy gate) + guardian.log observation point")
    return 0


if __name__ == "__main__":
    sys.exit(main())
