#!/usr/bin/env python3
"""Onboard the stepfun.com official key as a backup leg (2026-10-11).

Context: user provided a StepFun (阶跃星辰) platform key (Base58-decoded,
65-char). Direct probe against https://api.stepfun.com/v1: 33-model catalog
including step-5-preview (the exact id NewAPI serves), step-5-preview chat
200 in 2.1s, step-3.7-flash 200 in 1.0s.

Existing legs stay primary: ch205 step-plan (p0/w5, api.stepfun.ai/step_plan
plan key) and ch184 opencode-go zen relay (p0/w2) keep winning step traffic.
This channel is the same-tier LAST resort (p0/w1, auto_ban=1) so a dead plan
key or relay outage still leaves a direct-official path.

Verification mirrors add_kimi_welfare_leg.py: direct gate BEFORE write,
online backup, create/resume with drift check (skip PUT when complete),
abilities rebuild, admin channel test, gateway attribution must land on
ch205/ch184 (primary wins) or ch{new} only when observed disabled.

Usage:
  python3 scripts/ops/add_stepfun_official_leg.py            # dry-run
  STEP_FUN_KEY=*** python3 scripts/ops/add_stepfun_official_leg.py --apply
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
BASE_URL = "https://api.stepfun.com"
DIRECT_BASE = "https://api.stepfun.com/v1"
CHANNEL_NAME = "stepfun-official"
CHANNEL_TYPE = 1
MODELS = ["step-5-preview", "step-3.5-flash", "step-3.5-flash-2603", "step-3.7-flash"]
TEST_MODEL = "step-5-preview"
# 2026-10-12: bumped 0 -> 10 after ch205 (plan credit exhausted) and ch184
# (zen relay endpoint dead) stayed enabled-but-429ing; weighted random within
# p0 sent ~5/6 of step traffic into the dead ch205. Higher priority tier =
# always tried first; ch205/ch184 remain enabled as post-recovery fallback.
PRIORITY = 10
WEIGHT = 1
AUTO_BAN = 1
PRIMARY_TIER = (205, 184)


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
    destination = backup_dir / f"new-api-before-stepfun-official-{time.strftime('%Y%m%d-%H%M%S')}.db"
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
    missing = {"step-5-preview", "step-3.7-flash"} - ids
    if missing:
        raise RuntimeError(f"direct gate: catalog missing {sorted(missing)} (got {sorted(ids)})")
    body = json.dumps(
        {
            "model": "step-5-preview",
            "messages": [{"role": "user", "content": "Reply with exactly: SF_GATE_OK"}],
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
        if content:
            print(f"direct gate ok: catalog={len(ids)} step-5-preview completion non-empty ({len(content)} chars)")
            return
        last_err = f"empty content finish={choice.get('finish_reason')}"
    raise RuntimeError(f"direct gate: 200 but empty content x3: {last_err}")


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


def primary_states():
    rows = db_one(
        f"SELECT GROUP_CONCAT(status) FROM channels WHERE id IN ({','.join('?' * len(PRIMARY_TIER))})",
        PRIMARY_TIER,
    )
    return rows[0] if rows else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    args = parser.parse_args()

    print("plan:")
    print(f"  create: {CHANNEL_NAME} type={CHANNEL_TYPE} models={MODELS}")
    print(f"  base: {BASE_URL} (stepfun.com official direct)")
    print(f"  posture: same-tier LAST resort p{PRIORITY}/w{WEIGHT} auto_ban={AUTO_BAN}; "
          f"ch{list(PRIMARY_TIER)} (w5/w2) keep winning step traffic")
    print("  gates: direct key completion BEFORE write; online backup; read-back;")
    print(f"         abilities (default,1,{PRIORITY},{WEIGHT}) x{len(MODELS)}; admin test;")
    print("         attribution on ch205/ch184 (primary wins) or new leg only if observed dead")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    key = os.environ.get("STEP_FUN_KEY", "").strip()
    if not key:
        raise RuntimeError("STEP_FUN_KEY env var required")

    direct_gate(key)

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

    # gateway attribution: existing legs (ch205 w5 / ch184 w2) must keep
    # winning step-5-preview while healthy; new leg only if observed dead.
    gateway_key = read_gateway_key()
    status, usage, attr = gateway_chat(smoke, gateway_key, TEST_MODEL)
    for attempt in range(1, 4):
        if status == 200 or status not in (404, 408, 429, 500, 502, 503, 504, 524):
            break
        print(f"    retry {TEST_MODEL}: HTTP {status} (attempt {attempt}/3)")
        time.sleep(20)
        status, usage, attr = gateway_chat(smoke, gateway_key, TEST_MODEL)
    if status != 200:
        raise RuntimeError(f"gateway {TEST_MODEL} chat failed: HTTP {status}")
    if attr is None:
        raise RuntimeError(f"{TEST_MODEL}: no consumption-log attribution observed")
    if attr in PRIMARY_TIER:
        print(f"posture ok: {TEST_MODEL} 200 usage={usage.get('prompt_tokens')}/{usage.get('completion_tokens')} "
              f"attributed ch{attr} (existing leg wins; ch{new_id} idle as designed)")
    elif attr == new_id:
        new_state = db_one("SELECT status FROM channels WHERE id = ?", (new_id,))
        if new_state and int(new_state[0]) != 1:
            print(f"failover observed: attributed ch{new_id} with status={new_state[0]} "
                  f"(new leg disabled; check upstream)")
        else:
            raise RuntimeError(
                f"{TEST_MODEL} attributed ch{new_id} while ch{PRIMARY_TIER} still enabled — "
                "weight ordering not effective; investigate routing"
            )
    else:
        raise RuntimeError(f"{TEST_MODEL} attributed ch{attr}, expected ch{PRIMARY_TIER} or ch{new_id} — routing defect")

    print(f"DONE: ch{new_id} {CHANNEL_NAME} live at p{PRIORITY}/w{WEIGHT}; ch{list(PRIMARY_TIER)} untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
