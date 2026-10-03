#!/usr/bin/env python3
"""Onboard apichat.budsin.dev (aggregator) as a backup-only channel in local NewAPI (2026-10-03).

Shape follows scripts/ops/add_stepfun_step_plan_channel.py.

Design (evidence-backed):
- Upstream GET /v1/models lists 617 ids; candidate ids probed OK with real
  completions via direct upstream chat (2026-10-03).
- Exact-id strategy: no OMP models.yml / pricing changes needed (all four
  canonical ids already priced + routed locally). k3 rides model_mapping
  {"k3": "kimi-k3"} (upstream has no bare "k3" id).
- NewAPI priority direction: HIGHER number = served first (enabled opus-5
  carriers are p50-p54, documented fallbacks ch45/72 sit at p40). Backup
  posture therefore = priority BELOW each pool's enabled minimum:
  deepseek-v4-flash=30 (ch118), glm-5.3=40 (ch140-147), k3=50 (ch33),
  claude-opus-5=50 (ch18). Single value p20/w1 is strictly backup everywhere.
- qwen3.8-max + step-3.7-flash DROPPED from v1: both pools' only enabled
  carrier sits at p0 (ch89 w0 / ch144), leaving no lower backup tier;
  joining qwen at p0/w1 against ch89's w0 would hand this channel ALL
  qwen traffic. Revisit only with a dedicated posture decision.
- Policy-safe vs scripts/ops/newapi-local-smoke.py: CRITICAL_ABILITY_POSTURES
  pins existing (channel,model) rows only; MIN_ENABLED_CRITICAL_MODELS are
  floors; no zg-* aliases on this channel so no mapping rule triggers.

Pool depth effect (enabled channels):
  deepseek-v4-flash 1->2, k3 1->2, claude-opus-5 3->4, glm-5.3 6->7.

Dry-run by default; --apply mutates. Key via BUDSIN_KEY env (never printed).
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
BASE_URL = "https://apichat.budsin.dev"
CHANNEL_NAME = "budsin-apichat"
MODELS = [
    "deepseek-v4-flash",
    "glm-5.3",
    "claude-opus-5",
    "k3",
]
MODEL_MAPPING = {"k3": "kimi-k3"}
PRIMARY_MODEL = "deepseek-v4-flash"  # test_model: cheapest exact-id pool member
PRIORITY = 20
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
    destination = backup_dir / f"new-api-before-budsin-apichat-{stamp}.db"
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
        time.sleep(2)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    args = parser.parse_args()

    print("plan:")
    print(f"  create: {CHANNEL_NAME} models={','.join(MODELS)} prio={PRIORITY} w={WEIGHT}")
    print(f"  mapping: {json.dumps(MODEL_MAPPING)}  base={BASE_URL} (no /v1; NewAPI auto-appends)")
    print("  pricing: read-only verify (all four ids already priced)")
    print("  POST /api/channel/fix (dead-channel sweep; abilities rebuild)")
    print("  functional: admin channel test + gateway chain-health chat (attribution stays on primaries)")
    print("  posture: backup-only (priority below every enabled pool member)")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    key = os.environ.get("BUDSIN_KEY", "").strip()
    if not key:
        raise RuntimeError("BUDSIN_KEY env var required")

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    channels = fetch_channels(smoke, headers)
    by_name = {str(c.get("name")): c for c in channels}
    existing = by_name.get(CHANNEL_NAME)
    if existing is not None:
        # idempotent resume: a previous --apply may have created the channel
        # then failed later (observed 2026-10-03: pricing check stop). Reuse
        # only when the stored config matches this script's contract exactly.
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
        if isinstance(stored_raw, dict):
            stored_mapping_obj = stored_raw
        else:
            stored_mapping_obj = json.loads(str(stored_raw or "") or "{}")
        if stored_mapping_obj != MODEL_MAPPING:
            mismatch.append(f"model_mapping={stored_raw!r}")
        if mismatch:
            raise RuntimeError(
                f"channel {CHANNEL_NAME} (ch{existing['id']}) exists with drifted config: {mismatch}; "
                "manual reconciliation required (do not silently overwrite)"
            )
        reused_id = int(existing["id"])
        print(f"resume: reusing existing ch{reused_id} {CHANNEL_NAME} (config matches)")

    backup = online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    # 1. create channel (skipped on resume)
    if existing is not None:
        new_id = reused_id
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

    # 2. pricing: read-only parity report — never overwrite ratios.
    # glm-5.3 has NO ModelRatio entry while six channels serve it: it bills
    # at gateway default ratio today, so the new channel has parity with
    # existing carriers. Report, don't gate.
    row = db_one("SELECT value FROM options WHERE key = 'ModelRatio'")
    mr = json.loads(row[0]) if row and row[0] else {}
    row = db_one("SELECT value FROM options WHERE key = 'CompletionRatio'")
    cr = json.loads(row[0]) if row and row[0] else {}
    for m in MODELS:
        note = "gateway default (parity with existing carriers)" if m not in mr else f"ModelRatio={mr[m]}/{cr.get(m)}"
        print(f"  pricing: {m} -> {note}")

    # 3. fix + verify abilities for all models
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

    # 4. admin channel test (real upstream call through NewAPI channel machinery)
    t_status, t_body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/test/{new_id}?model={PRIMARY_MODEL}",
        headers=headers,
        timeout=120,
    )
    t_ok = isinstance(t_body, dict) and t_body.get("success")
    t_ms = t_body.get("time") if isinstance(t_body, dict) else None
    print(f"channel test: HTTP {t_status} success={t_ok} time={t_ms} model={PRIMARY_MODEL}")
    if not t_ok:
        message = t_body.get("message") if isinstance(t_body, dict) else None
        raise RuntimeError(f"channel test failed: {message!r}")

    # 5. gateway chain-health: glm-5.3 chat must 200; attribution expected on an
    # existing lower-priority channel, NOT the new backup (asserted, not hidden).
    gateway_key = read_gateway_key()
    mark = time.time()
    status, body = smoke.http_json(
        f"{GATEWAY_BASE}/v1/chat/completions",
        method="POST",
        body={
            "model": "glm-5.3",
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": 16,
        },
        headers={"Authorization": f"Bearer {gateway_key}"},
        timeout=180,
    )
    if status != 200:
        raise RuntimeError(f"gateway chain-health chat failed: HTTP {status}: {json.dumps(body)[:200]}")
    usage = body.get("usage") or {}
    attr = None
    for _ in range(6):
        row_log = db_one(
            "SELECT channel_id FROM logs WHERE model_name = ? AND created_at > ? AND type = 2 "
            "ORDER BY id DESC LIMIT 1",
            ("glm-5.3", mark),
        )
        if row_log:
            attr = int(row_log[0])
            break
        time.sleep(5)
    if attr == new_id:
        raise RuntimeError("unexpected: backup channel served glm-5.3 while primaries are enabled")
    print(f"chain-health OK: glm-5.3 200 usage={usage.get('prompt_tokens')}/{usage.get('completion_tokens')} "
          f"attributed ch{attr} (existing primary, as designed)")

    print(f"DONE: ch{new_id} {CHANNEL_NAME} live as backup; models: {','.join(MODELS)}")
    print("NEXT: run scripts/ops/newapi-local-smoke.py (policy gate) + 15721 Anthropic smoke")
    return 0


if __name__ == "__main__":
    sys.exit(main())
