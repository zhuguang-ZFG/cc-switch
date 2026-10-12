#!/usr/bin/env python3
"""Onboard the welfare kimi-official key as the PRIMARY kimi leg (2026-10-11).

Context (README ledger 2026-10-11 + k3-outage doc):
- The kimi family (k3/k3-256k/kimi-for-coding/kimi-for-coding-highspeed/zg-k3)
  was single-leg on ch33 (the user's own official subscription, p50/w10,
  weekly-quota outage history). A community-floating "Kimi K3 (1M) 官方套餐"
  key (sk-kimi-* format) was verified working directly against
  https://api.kimi.com/coding/v1 (same 4-id catalog as ch33; k3 completion
  200 in 2.4s; SSE ok).
- USER INSTRUCTION: 先用这个，再用我自己的订阅 -> this key carries kimi
  traffic FIRST; ch33 stays untouched as the automatic fallback.

Posture: PRIMARY p60/w10 auto_ban=1 (public key can be revoked at any time;
auto-ban demotes it and traffic failbacks to ch33 p50). Base/models/mapping
mirror ch33 exactly (type=1 OpenAI-compatible).

Verification: direct key gate (models + real completion) BEFORE any write;
online DB backup; create (or drift-checked PUT resume); models read-back;
abilities rebuild must reach (default,1,60,10) x5; admin channel test;
gateway attribution MUST land on the new channel (primary wins) — ch33
attribution is only acceptable when the new channel is observed
disabled/auto-banned at probe time (failback path). ch33 row is asserted
unchanged (status/priority/weight) before and after.

Usage:
  python3 scripts/ops/add_kimi_welfare_leg.py            # dry-run
  KIMI_WELFARE_KEY=*** scripts/ops/add_kimi_welfare_leg.py --apply
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
DB_PATH = Path.home() / ".new-api-local" / "new-api.db"
GATEWAY_BASE = "http://127.0.0.1:3002"
BASE_URL = "https://api.kimi.com/coding"
DIRECT_BASE = "https://api.kimi.com/coding/v1"
CHANNEL_NAME = "kimi-official-k3-welfare"
CHANNEL_TYPE = 1  # OpenAI-compatible, mirrors ch33
MODELS = ["k3", "k3-256k", "kimi-for-coding", "kimi-for-coding-highspeed", "zg-k3"]
MODEL_MAPPING = {"zg-k3": "k3"}
TEST_MODEL = "kimi-for-coding-highspeed"
# PRIMARY tier per user instruction; ch33 (user's own subscription) demotes
# to fallback by priority alone and is never written by this script.
PRIORITY = 60
WEIGHT = 10
AUTO_BAN = 1
FALLBACK_ID = 33


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
    backup_dir.mkdir(exist_ok=True)
    destination = backup_dir / f"new-api-before-kimi-welfare-{time.strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(db_path, timeout=30)
    dst = sqlite3.connect(destination, timeout=30)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return destination

def fetch_channels(smoke, headers):
    # this fork pages the admin list: data={items,page,page_size,total,type_counts}
    _status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=500", method="GET", headers=headers
    )
    data = (body or {}).get("data")
    items = data.get("items") if isinstance(data, dict) else data
    return [i for i in (items or []) if isinstance(i, dict)]


def read_gateway_key() -> str:
    """OMP zg-newapi apiKey from live models.yml (never printed)."""
    text = (Path.home() / ".omp" / "agent" / "models.yml").read_text(encoding="utf-8")
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


def direct_gate(key: str) -> None:
    """The key must serve a real completion upstream BEFORE any DB/API write."""
    import urllib.request

    req = urllib.request.Request(
        DIRECT_BASE + "/models", headers={"Authorization": f"Bearer {key}"}
    )
    data = json.load(urllib.request.urlopen(req, timeout=30))
    ids = {m["id"] for m in data.get("data", [])}
    missing = {"k3", "kimi-for-coding"} - ids
    if missing:
        raise RuntimeError(f"direct gate: catalog missing {sorted(missing)} (got {sorted(ids)})")
    # reasoning model: a small budget can return 200 with empty content
    # (thinking ate it all) — not a key failure. Probe with headroom.
    body = json.dumps(
        {
            "model": "k3",
            "messages": [{"role": "user", "content": "Reply with exactly: WELFARE_GATE_OK"}],
            "max_tokens": 800,
        }
    ).encode()
    last_err = "no attempt"
    for attempt in range(3):
        if attempt:
            time.sleep(10)
        req = urllib.request.Request(
            DIRECT_BASE + "/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        r = json.load(urllib.request.urlopen(req, timeout=120))
        choice = (r.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = (msg.get("content") or "").strip()
        reasoning = (msg.get("reasoning_content") or "").strip()
        if content or reasoning:
            print(f"direct gate ok: catalog={len(ids)} k3 completion ok "
                  f"(content={len(content)} chars, reasoning={len(reasoning)} chars)")
            return
        last_err = f"empty content+reasoning finish={choice.get('finish_reason')}"
    raise RuntimeError(f"direct gate: 200 but no usable output x3: {last_err}")


def gateway_chat(smoke, gateway_key: str, model: str, max_tokens: int = 200):
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
    # consumption rows can land >30s after a 200 response; re-probe (bounded)
    # and widen the window rather than declare "no attribution" on write lag.
    for attempt in range(3):
        if attempt:
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
            mark = time.time() - 240
        for _ in range(8):
            row_log = db_one(
                "SELECT channel_id FROM logs WHERE model_name = ? AND created_at > ? AND type = 2 "
                "ORDER BY id DESC LIMIT 1",
                (model, mark),
            )
            if row_log:
                attr = int(row_log[0])
                break
            time.sleep(5)
        if attr is not None:
            break
    return status, usage, attr


def gateway_chat_retry(smoke, gateway_key: str, model: str, attempts: int = 4, pause: int = 20):
    last = gateway_chat(smoke, gateway_key, model)
    for i in range(1, attempts):
        if last[0] == 200 or last[0] not in (404, 408, 429, 500, 502, 503, 504, 524):
            return last
        print(f"    retry {model}: HTTP {last[0]} (attempt {i}/{attempts})")
        time.sleep(pause)
        last = gateway_chat(smoke, gateway_key, model)
    return last


def fallback_state():
    return db_one("SELECT status, priority, weight FROM channels WHERE id = ?", (FALLBACK_ID,))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    args = parser.parse_args()

    print("plan:")
    print(f"  create: {CHANNEL_NAME} type={CHANNEL_TYPE} models={MODELS}")
    print(f"  base: {BASE_URL} (mirrors ch{FALLBACK_ID})  mapping: {MODEL_MAPPING}")
    print(f"  posture: PRIMARY p{PRIORITY}/w{WEIGHT} auto_ban={AUTO_BAN} (user: 先用这个; "
          f"ch{FALLBACK_ID} 订阅 p50 = auto fallback, never written)")
    print("  gates: direct key completion BEFORE write; online backup; read-back;")
    print("         abilities (default,1,%d,%d) x%d; admin test;" % (PRIORITY, WEIGHT, len(MODELS)))
    print("         attribution MUST land on new leg (fallback only if new leg observed dead)")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    key = os.environ.get("KIMI_WELFARE_KEY", "").strip()
    if not key:
        raise RuntimeError("KIMI_WELFARE_KEY env var required")

    direct_gate(key)
    fb_before = fallback_state()
    if not fb_before or tuple(fb_before) != (1, 50, 10):
        raise RuntimeError(f"ch{FALLBACK_ID} unexpected state {fb_before}; manual reconciliation required")

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    channels = fetch_channels(smoke, headers)
    by_name = {str(c.get("name")): c for c in channels}
    existing = by_name.get(CHANNEL_NAME)
    new_id: int | None = None
    if existing is not None:
        mismatch = []
        if str(existing.get("base_url") or "") != BASE_URL:
            mismatch.append(f"base_url={existing.get('base_url')!r}")
        if str(existing.get("models") or "") != ",".join(MODELS):
            mismatch.append(f"models={existing.get('models')!r}")
        if int(existing.get("type") or -1) != CHANNEL_TYPE:
            mismatch.append(f"type={existing.get('type')!r}")
        stored_priority = existing.get("priority")
        if int(stored_priority if stored_priority is not None else -1) != PRIORITY:
            mismatch.append(f"priority={stored_priority!r}")
        stored_weight = existing.get("weight")
        if int(stored_weight if stored_weight is not None else -1) != WEIGHT:
            mismatch.append(f"weight={stored_weight!r}")
        stored_raw = existing.get("model_mapping")
        stored_mapping_obj = stored_raw if isinstance(stored_raw, dict) else json.loads(str(stored_raw or "") or "{}")
        if stored_mapping_obj != MODEL_MAPPING:
            mismatch.append(f"model_mapping={stored_raw!r}")
        if mismatch:
            raise RuntimeError(
                f"channel {CHANNEL_NAME} (ch{existing['id']}) exists with drifted config: {mismatch}; "
                "manual reconciliation required (do not silently overwrite)"
            )
        new_id = int(existing["id"])
        if str(existing.get("key") or "") == key:
            print(f"resume: ch{new_id} complete (config+key match); skipping PUT (fork PUT clobber history)")
        else:
            print(f"resume: key rotation via PUT on ch{new_id} {CHANNEL_NAME}")
            payload = {
                "id": new_id,
                "name": CHANNEL_NAME,
                "type": CHANNEL_TYPE,
                "base_url": BASE_URL,
                "key": key,
                "models": ",".join(MODELS),
                "model_mapping": json.dumps(MODEL_MAPPING, ensure_ascii=False),
                "group": "default",
                "priority": PRIORITY,
                "weight": WEIGHT,
                "auto_ban": AUTO_BAN,
                "test_model": TEST_MODEL,
            }
            status, body = smoke.http_json(
                f"{smoke.NEWAPI_BASE}/api/channel/", method="PUT", body=payload, headers=headers
            )
            if status != 200 or not isinstance(body, dict) or not body.get("success"):
                message = body.get("message") if isinstance(body, dict) else None
                raise RuntimeError(f"channel PUT failed: HTTP {status} message={message!r}")
            print(f"updated ch{new_id}")
    else:
        backup = online_backup(DB_PATH)
        print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes)")
        payload = {
            "name": CHANNEL_NAME,
            "type": CHANNEL_TYPE,
            "base_url": BASE_URL,
            "key": key,
            "models": ",".join(MODELS),
            "model_mapping": json.dumps(MODEL_MAPPING, ensure_ascii=False),
            "group": "default",
            "priority": PRIORITY,
            "weight": WEIGHT,
            "auto_ban": AUTO_BAN,
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

    after = fetch_channels(smoke, headers)
    match = [c for c in after if int(c.get("id") or 0) == new_id]
    if not match:
        raise RuntimeError(f"ch{new_id} not visible after create/update")
    stored_final = set(str(match[0].get("models") or "").split(",")) - {""}
    if stored_final != set(MODELS):
        raise RuntimeError(f"ch{new_id} read-back models mismatch: delta={sorted(set(MODELS) ^ stored_final)}")
    print(f"read-back ok: ch{new_id} models={len(stored_final)}")

    for opt_key in ("ModelRatio", "CompletionRatio"):
        row = db_one("SELECT value FROM options WHERE key = ?", (opt_key,))
        table = json.loads(row[0]) if row and row[0] else {}
        for m in MODELS:
            print(f"  pricing: {m} -> {opt_key}={table.get(m)}")

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

    t_ok = False
    t_body: dict = {}
    for attempt in range(1, 4):
        t_status, t_body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/test/{new_id}?model={TEST_MODEL}",
            headers=headers,
            timeout=120,
        )
        t_ok = isinstance(t_body, dict) and t_body.get("success")
        t_ms = t_body.get("time") if isinstance(t_body, dict) else None
        print(f"channel test: HTTP {t_status} success={t_ok} time={t_ms} model={TEST_MODEL}")
        if t_ok or attempt == 3:
            break
        time.sleep(20)
    if not t_ok:
        message = t_body.get("message") if isinstance(t_body, dict) else None
        raise RuntimeError(f"channel test failed: {message!r} — key may already be revoked; nothing else changed")

    # gateway attribution: welfare-first posture. New leg p60 MUST win while
    # healthy; ch33 attribution is only tolerated when the new leg is
    # observed dead (auto-banned/disabled) at probe time = designed failback.
    gateway_key = read_gateway_key()
    status, usage, attr = gateway_chat_retry(smoke, gateway_key, TEST_MODEL)
    if status != 200:
        raise RuntimeError(f"gateway {TEST_MODEL} chat failed: HTTP {status}")
    if attr is None:
        raise RuntimeError(f"{TEST_MODEL}: no consumption-log attribution observed")
    if attr == new_id:
        print(f"posture ok: {TEST_MODEL} 200 usage={usage.get('prompt_tokens')}/{usage.get('completion_tokens')} "
              f"attributed ch{new_id} (welfare leg wins as primary)")
    elif attr == FALLBACK_ID:
        now = fallback_state()
        new_state = db_one("SELECT status FROM channels WHERE id = ?", (new_id,))
        if new_state and int(new_state[0]) != 1:
            print(f"failback observed: attributed ch{attr}; ch{new_id} status={new_state[0]} "
                  f"(auto-ban demoted the welfare key; ch{FALLBACK_ID} state={now} carries traffic)")
        else:
            raise RuntimeError(
                f"{TEST_MODEL} attributed ch{FALLBACK_ID} while ch{new_id} still enabled — "
                "welfare-first priority not effective; investigate routing before trusting the posture"
            )
    else:
        raise RuntimeError(f"{TEST_MODEL} attributed ch{attr}, expected ch{new_id} or ch{FALLBACK_ID} — routing defect")

    status2, usage2, attr2 = gateway_chat_retry(smoke, gateway_key, "k3")
    if status2 != 200:
        raise RuntimeError(f"gateway k3 chat failed: HTTP {status2}")
    print(f"k3 probe 200 usage={usage2.get('prompt_tokens')}/{usage2.get('completion_tokens')} attributed ch{attr2}")

    fb_after = fallback_state()
    if tuple(fb_after) != tuple(fb_before):
        raise RuntimeError(f"ch{FALLBACK_ID} state changed by this run: {fb_before} -> {fb_after}")
    print(f"fallback intact: ch{FALLBACK_ID} status/priority/weight = {fb_after} (untouched)")

    print(f"DONE: ch{new_id} {CHANNEL_NAME} PRIMARY live at p{PRIORITY}/w{WEIGHT}; ch{FALLBACK_ID} = auto fallback")
    print("NEXT: README ledger + attribution re-check after any auto-ban; ss2a-kimi staged leg now redundant")
    return 0


if __name__ == "__main__":
    sys.exit(main())
