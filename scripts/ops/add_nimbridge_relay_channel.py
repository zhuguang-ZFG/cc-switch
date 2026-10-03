#!/usr/bin/env python3
"""Onboard nimbridge relay (207.57.126.219:3333) into local NewAPI (2026-10-04).

Shape follows scripts/ops/add_budsin_apichat_channel.py (idempotent resume).

Design (evidence-backed):
- Read-only probes (2026-10-04): /v1/models lists 3 ids; deepseek-flash 200
  real completion; glm-5.3-flash 200; kimi-k3 403 key-not-entitled
  ("nimbridge: 该客户端密钥无权使用模型 kimi-k3" - relay self-identifies).
- Scope (user-approved): glm-5.3-flash exact-id (revives the flash tier -
  ch121 bai dead, zero enabled channels locally) + deepseek-v4-flash via
  mapping {"deepseek-v4-flash": "deepseek-flash"} (second backup leg behind
  ch148 budsin; ch118 seeseed dropped the model upstream).
- Posture: priority -10 / weight 1, bottom-tier backup (matches ch148).
  glm-5.3-flash pool is empty so ch149 is sole carrier there by definition;
  deepseek-v4-flash joins ch148 at the same -10 tier (w1/w1 split = redundancy).
- Unknown upstream billing -> backup tier only; auto_ban=1 fail-closed for
  auth/quota-class rejections (5xx NOT covered - ch89 evidence 2026-10-04).

Dry-run by default; --apply mutates. Key via NB_KEY env (never printed).
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
BASE_URL = "https://207.57.126.219:3333"
CHANNEL_NAME = "nimbridge-relay"
MODELS = ["glm-5.3-flash", "deepseek-v4-flash"]
MODEL_MAPPING = {"deepseek-v4-flash": "deepseek-flash"}
PRIMARY_MODEL = "glm-5.3-flash"  # test_model: sole-carrier pool revival
PRIORITY = -10
WEIGHT = 1


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
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
    destination = backup_dir / f"new-api-before-nimbridge-{stamp}.db"
    with closing(sqlite3.connect(str(db_path))) as source:
        with closing(sqlite3.connect(str(destination))) as target:
            source.backup(target)
    with closing(sqlite3.connect(f"file:{destination.as_posix()}?mode=ro", uri=True)) as check:
        row = check.execute("PRAGMA integrity_check").fetchone()
    if not row or row[0] != "ok":
        raise RuntimeError(f"backup integrity check failed: {row!r}")
    return destination


def fetch_channels(smoke, headers):
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=200", headers=headers
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"channel list failed: HTTP {status}")
    items = (body.get("data") or {}).get("items") or []
    return [i for i in items if isinstance(i, dict)]


def read_gateway_key() -> str:
    """OMP zg-newapi apiKey from live models.yml (never printed)."""
    import re

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
        time.sleep(2)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    args = parser.parse_args()

    print("plan:")
    print(f"  create: {CHANNEL_NAME} models={','.join(MODELS)} prio={PRIORITY} w={WEIGHT}")
    print(f"  mapping: {json.dumps(MODEL_MAPPING)}  base={BASE_URL} (no /v1; NewAPI auto-appends)")
    print("  posture: bottom-tier backup (matches ch148 at -10)")
    print("  pricing: read-only report (glm-5.3-flash official $0.15/$0.50; deepseek-v4-flash already priced)")
    print("  POST /api/channel/fix (abilities rebuild)")
    print("  functional: admin channel test x2 + glm-5.3-flash gateway chat (sole carrier) + envelope precheck probe")

    if not args.apply:
        print("dry-run: no changes made")
        return 0

    key = os.environ.get("NB_KEY", "").strip()
    if not key:
        raise RuntimeError("NB_KEY env var required")

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    reused_id = None
    channels = fetch_channels(smoke, headers)
    by_name = {str(c.get("name")): c for c in channels}
    existing = by_name.get(CHANNEL_NAME)
    if existing is not None:
        mismatch = []
        if str(existing.get("base_url") or "") != BASE_URL:
            mismatch.append(f"base_url={existing.get('base_url')!r}")
        if str(existing.get("models") or "") != ",".join(MODELS):
            mismatch.append(f"models={existing.get('models')!r}")
        if int(existing.get("priority") or 0) != PRIORITY:
            mismatch.append(f"priority={existing.get('priority')!r}")
        stored_raw = existing.get("model_mapping")
        stored_obj = stored_raw if isinstance(stored_raw, dict) else json.loads(str(stored_raw or "") or "{}")
        if stored_obj != MODEL_MAPPING:
            mismatch.append(f"model_mapping={stored_raw!r}")
        if mismatch:
            raise RuntimeError(
                f"channel {CHANNEL_NAME} (ch{existing['id']}) exists with drifted config: {mismatch}; "
                "manual reconciliation required"
            )
        reused_id = int(existing["id"])
        print(f"resume: reusing existing ch{reused_id} {CHANNEL_NAME} (config matches)")

    backup = online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    # 1. create channel (skipped on resume)
    if existing is not None:
        new_id = reused_id
        print(f"using ch{new_id} (resumed)")
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
            "test_model": PRIMARY_MODEL,
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

    # 2. pricing: read-only parity report
    row = db_one("SELECT value FROM options WHERE key = 'ModelRatio'")
    mr = json.loads(row[0]) if row and row[0] else {}
    print(f"  pricing: glm-5.3-flash -> {mr.get('glm-5.3-flash', 'unset (official card $0.15/$0.50, set separately)')}")
    print(f"  pricing: deepseek-v4-flash -> {mr.get('deepseek-v4-flash')}")

    # 3. fix + verify abilities
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

    # 4. admin channel tests (real upstream calls through NewAPI machinery;
    #    also proves the fork tolerates the self-signed IP cert - or fails here)
    for m in MODELS:
        t_status, t_body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/test/{new_id}?model={m}",
            headers=headers,
            timeout=120,
        )
        t_ok = isinstance(t_body, dict) and t_body.get("success")
        t_ms = t_body.get("time") if isinstance(t_body, dict) else None
        print(f"channel test {m}: HTTP {t_status} success={t_ok} time={t_ms}")
        if not t_ok:
            message = t_body.get("message") if isinstance(t_body, dict) else None
            raise RuntimeError(f"channel test failed for {m}: {message!r}")

    # 5. envelope precheck probe: max_tokens=131072 (intern-style declared-envelope
    #    quota precheck exists on some upstreams; 429 = precheck present)
    gateway_key = read_gateway_key()
    status, body = smoke.http_json(
        f"{GATEWAY_BASE}/v1/chat/completions",
        method="POST",
        body={
            "model": "glm-5.3-flash",
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": 131072,
        },
        headers={"Authorization": f"Bearer {gateway_key}"},
        timeout=180,
    )
    if status == 429:
        print("envelope: 429 on declared 131072 -> upstream precheck present; declare 131072/32768 in models.yml")
    elif status == 200:
        usage = (body.get("usage") or {}) if isinstance(body, dict) else {}
        print(f"envelope: 200 on declared 131072 usage={usage.get('prompt_tokens')}/{usage.get('completion_tokens')} (no precheck)")
    else:
        raise RuntimeError(f"envelope probe failed: HTTP {status}: {json.dumps(body)[:200]}")

    # 6. attribution: glm-5.3-flash must land on ch149 (sole carrier)
    mark = time.time()
    status, body = smoke.http_json(
        f"{GATEWAY_BASE}/v1/chat/completions",
        method="POST",
        body={
            "model": "glm-5.3-flash",
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": 32768,
        },
        headers={"Authorization": f"Bearer {gateway_key}"},
        timeout=180,
    )
    if status != 200:
        raise RuntimeError(f"gateway chat failed: HTTP {status}: {json.dumps(body)[:200]}")
    attr = None
    for _ in range(6):
        row_log = db_one(
            "SELECT channel_id FROM logs WHERE model_name = ? AND created_at > ? AND type = 2 "
            "ORDER BY id DESC LIMIT 1",
            ("glm-5.3-flash", mark),
        )
        if row_log:
            attr = int(row_log[0])
            break
        time.sleep(5)
    if attr != new_id:
        raise RuntimeError(f"attribution mismatch: logs ch{attr}, expected ch{new_id}")
    usage = (body.get("usage") or {}) if isinstance(body, dict) else {}
    print(f"attribution OK: glm-5.3-flash -> ch{attr} usage={usage.get('prompt_tokens')}/{usage.get('completion_tokens')}")

    print(f"DONE: ch{new_id} {CHANNEL_NAME} live; models: {','.join(MODELS)}")
    print("NEXT: re-add glm-5.3-flash to models.yml, run smoke gate, write runbook")
    return 0


if __name__ == "__main__":
    sys.exit(main())
