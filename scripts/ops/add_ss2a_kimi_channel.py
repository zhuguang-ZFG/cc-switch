#!/usr/bin/env python3
"""Onboard ss2a.top kimi group as the second leg for the kimi family (2026-10-09).

Context (docs/ops/k3-outage-misattributed-glm53-2026-10-09.md):
- 2026-10-09 k3 went single-leg: ch33 kimi-official-k3 is the ONLY enabled
  channel serving k3/k3-256k/kimi-for-coding/kimi-for-coding-highspeed
  (ch115/148 + tokenrhythm pool parked, quota-dead). ch33 rides a weekly
  official quota that already caused one full outage this week.
- A new ss2a.top key (separate group from the glm-5.3 ch183 key) unlocks
  exactly these 4 kimi ids, Anthropic protocol only:
  * /v1/messages  -> 200 real completions (all 4 ids probed)
  * /v1/chat/completions -> 502 "Upstream access forbidden" (group policy)
  * glm-5.3 / claude-* ids -> 404 "not supported ... in this group"
  => channel must be type=14 (Anthropic), exact ids; the zg-k3 Cursor BYOK
  alias rides along with mapping zg-k3->k3, mirroring ch33's surface
  (zg-k3 is live Cursor BYOK traffic and otherwise single-sourced on ch33).
- Observed: upstream account pool can 503 "Service temporarily unavailable"
  cross-protocol for minutes at a time; retries with pause are bounded.

Posture: ch33 (p50/w10) stays PRIMARY and must keep winning kimi traffic
while healthy; this channel is a p20/w1 backup leg, auto_ban=1.
Verification: read-back models, abilities rebuild, admin channel test
(real upstream call through NewAPI's anthropic adaptor), then gateway
attribution for kimi-for-coding-highspeed must land on ch33 (primary wins,
backup idle) or ch{new} (ch33 not serving = failover path), never anything
else. OMP k3:max E2E runs separately (test_omp_routes.py pattern).

Usage:
  py scripts/ops/add_ss2a_kimi_channel.py            # dry-run (plan only)
  SS2A_KIMI_KEY=... py scripts/ops/add_ss2a_kimi_channel.py --apply
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
# base_url WITHOUT /v1: NewAPI's anthropic adaptor appends /v1/messages
BASE_URL = "https://ss2a.top"
CHANNEL_NAME = "ss2a-kimi"
CHANNEL_TYPE = 14  # Anthropic protocol; OpenAI path is forbidden for this key group
MODELS = ["k3", "k3-256k", "kimi-for-coding", "kimi-for-coding-highspeed", "zg-k3"]
MODEL_MAPPING: dict[str, str] = {"zg-k3": "k3"}  # real ids exact; alias mirrors ch33
TEST_MODEL = "kimi-for-coding-highspeed"  # fastest observed (~4.6s), text-first
# BACKUP tier: ch33 kimi-official-k3 (p50/w10) stays primary and must win
# kimi traffic while its weekly quota holds; this leg only carries failover.
PRIORITY = 20
WEIGHT = 1
AUTO_BAN = 1
PRIMARY_TIER = (33,)


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
    destination = backup_dir / f"new-api-before-ss2a-kimi-{time.strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(db_path, timeout=30)
    dst = sqlite3.connect(destination, timeout=30)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return destination


def fetch_channels(smoke, headers):
    _status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/", method="GET", headers=headers
    )
    items = (body or {}).get("data") or []
    return [i for i in items if isinstance(i, dict)]


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


def gateway_chat(smoke, gateway_key: str, model: str, max_tokens: int = 200):
    """One gateway chat; returns (status, usage, attribution_channel_id).

    max_tokens is intentionally >32: the kimi models are reasoning models and
    a tiny budget can return 200 with empty content.
    """
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


def gateway_chat_retry(smoke, gateway_key: str, model: str, attempts: int = 4, pause: int = 20):
    """gateway_chat with bounded retries on the transient failure classes only.

    404 = abilities cache race right after channel/fix; 408/429 = per-user
    concurrency/rate limits on these relays; 5xx = upstream account-pool
    blips (ss2a kimi group 503s for minutes at a time).
    """
    last = gateway_chat(smoke, gateway_key, model)
    for i in range(1, attempts):
        if last[0] == 200 or last[0] not in (404, 408, 429, 500, 502, 503, 504, 524):
            return last
        print(f"    retry {model}: HTTP {last[0]} (attempt {i}/{attempts})")
        time.sleep(pause)
        last = gateway_chat(smoke, gateway_key, model)
    return last


def primary_status() -> int | None:
    row = db_one("SELECT status FROM channels WHERE id = ?", (PRIMARY_TIER[0],))
    return int(row[0]) if row else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    args = parser.parse_args()

    print("plan:")
    print(f"  create: {CHANNEL_NAME} type={CHANNEL_TYPE} models={MODELS}")
    print(f"  base: {BASE_URL} (no /v1; anthropic adaptor appends /v1/messages)  mapping: {MODEL_MAPPING}")
    print(f"  posture: BACKUP p{PRIORITY}/w{WEIGHT} auto_ban={AUTO_BAN}; primary ch{list(PRIMARY_TIER)} (p50) keeps winning kimi traffic")
    print("  pricing: read-only parity report (never overwrite ratios)")
    print("  POST /api/channel/fix (abilities rebuild)")
    print("  functional: admin channel test (direct upstream) + gateway attribution (primary-wins assert)")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    key = os.environ.get("SS2A_KIMI_KEY", "").strip()
    if not key:
        raise RuntimeError("SS2A_KIMI_KEY env var required")

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
        if int(existing.get("priority") or -1) != PRIORITY:
            mismatch.append(f"priority={existing.get('priority')!r}")
        if int(existing.get("weight") or -1) != WEIGHT:
            mismatch.append(f"weight={existing.get('weight')!r}")
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
        print(f"resume: reusing existing ch{new_id} {CHANNEL_NAME}")
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

    # read-back: stored model set must equal MODELS exactly (catches silent drops)
    after = fetch_channels(smoke, headers)
    match = [c for c in after if int(c.get("id") or 0) == new_id]
    if not match:
        raise RuntimeError(f"ch{new_id} not visible after create/update")
    stored_final = set(str(match[0].get("models") or "").split(",")) - {""}
    if stored_final != set(MODELS):
        raise RuntimeError(f"ch{new_id} read-back models mismatch: delta={sorted(set(MODELS) ^ stored_final)}")
    print(f"read-back ok: ch{new_id} models={len(stored_final)}")

    # pricing: read-only parity report
    for opt_key in ("ModelRatio", "CompletionRatio"):
        row = db_one("SELECT value FROM options WHERE key = ?", (opt_key,))
        table = json.loads(row[0]) if row and row[0] else {}
        for m in MODELS:
            print(f"  pricing: {m} -> {opt_key}={table.get(m)}")

    # fix + verify abilities
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

    # admin channel test: real upstream call through NewAPI's anthropic adaptor.
    # This is the ONLY direct proof of the new leg (it never wins gateway
    # traffic while ch33 is healthy, by design).
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
        raise RuntimeError(
            f"channel test failed: {message!r} — ss2a kimi pool 503s for minutes at a time; "
            "re-run when the group recovers (script resumes via PUT)"
        )

    # gateway attribution: primary-wins posture check.
    # ch33 live -> kimi traffic MUST stay on ch33 (backup stays idle).
    # ch33 not serving -> attribution on ch{new_id} is the designed failover.
    gateway_key = read_gateway_key()
    status, usage, attr = gateway_chat_retry(smoke, gateway_key, TEST_MODEL)
    if status != 200:
        raise RuntimeError(f"gateway {TEST_MODEL} chat failed: HTTP {status}")
    if attr is None:
        raise RuntimeError(f"{TEST_MODEL}: no consumption-log attribution observed")
    allowed = {new_id} | set(PRIMARY_TIER)
    if attr not in allowed:
        raise RuntimeError(
            f"{TEST_MODEL} attributed ch{attr}, expected ch{PRIMARY_TIER[0]} (primary wins) "
            f"or ch{new_id} (failover) — anything else is a routing defect"
        )
    p_stat = primary_status()
    if attr == PRIMARY_TIER[0]:
        print(
            f"posture ok: {TEST_MODEL} 200 usage={usage.get('prompt_tokens')}/{usage.get('completion_tokens')} "
            f"attributed ch{attr} (primary ch33 status={p_stat} wins; backup ch{new_id} idle)"
        )
    else:
        print(
            f"failover path: {TEST_MODEL} 200 attributed ch{attr} (ch33 status={p_stat} not serving; "
            f"backup ch{new_id} carried the request)"
        )

    print(f"DONE: ch{new_id} {CHANNEL_NAME} BACKUP live at p{PRIORITY}/w{WEIGHT}; primary ch{list(PRIMARY_TIER)} intact")
    print("NEXT: newapi-local-smoke.py (policy gate) + README ledger + runbook docs/ops/ + OMP models.yml revive check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
