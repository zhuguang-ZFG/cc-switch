#!/usr/bin/env python3
"""Onboard ss2a.top as a backup-tier glm-5.3 carrier in local NewAPI (2026-10-08).

User-provided key via SS2A_KEY env only (never printed, never persisted).
Upstream self-identifies as a NewAPI-style relay (request_id + `2026...`-shaped
completion ids fronting a Zhipu "max"-plan official account for GLM-5.3); CF is
in front but bans no UA we tested.

Live probe 2026-10-08 (direct, chat/completions):
  GET /v1/models -> 200, exactly one id: glm-5.3 (extra `display_name` field).
  non-stream 200/4.5s content="SS2A_OK" (reasoning_content present);
  SSE 200, 129 data frames + [DONE] + usage; reasoning_effort="max" accepted 200.
  glm-5.3-max / glm-5.3:max -> 404 model_not_found (only the bare id exists).
  UA matrix curl / Go-http-client/1.1 / empty / python-urllib all 200 -> no
  header_override needed. 2 concurrent chats 200/3.1s+4.4s.
  No /v1/dashboard/billing/subscription, no /api/status (404) -> no balance API.
Conformance canary (`probe_untrusted_openai_provider.py --run`) reports
  empty-semantic-output x2 + tool-arguments-invalid x1, but its payload pins
  max_tokens=32 while glm-5.3 is a reasoning model (reasoning_tokens alone were
  53/26 in the re-runs) -> the issues are a canary budget artifact, NOT a relay
  defect. Re-probe with max_tokens=800: semantic exact match, and the forced
  report_canary tool call decodes to {"value":"CANARY_TOOL_OK"} -> valid.
  Cache probe: repeat_cache_observed=true, second_cache_read_tokens=640,
  suspicious_first_request_cache_hit=false.

POSTURE history (NewAPI dispatches HIGHER priority value first):
- 2026-10-08 onboarded at p-20/w1 as backup tier; user mandate "为主档" on the
  same day promoted this channel to the glm-5.3 PRIMARY (p50/w5, one-shot PUT,
  backup new-api-before-ss2a-promote-20261008-233550.db).
- glm-5.3 fallback tier: ch140/141/142/143/146 intern-discovery (p40/w1) — must
  NOT win while the ss2a primary serves; long disabled backup pools: ch147 (p40),
  ch148 (p10-), ch150-170 tokenrhythm (p-30), ch179 (p-30).
- This script now encodes the PRIMARY posture. Promoting/demoting the channel
  changes these constants and re-verifies the new winner; do not edit the DB
  by hand.

Precedent: add_hubway_channel.py / add_vsakura_gpt_channel.py (relay backup tier,
p-20/w1). Pricing: read-only parity report, never overwrite ratios. Idempotent
resume with drift detection; DB online backup before any write.
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
# base_url WITHOUT /v1: NewAPI auto-appends /v1/chat/completions
BASE_URL = "https://ss2a.top"
CHANNEL_NAME = "ss2a"
MODELS = ["glm-5.3"]
MODEL_MAPPING: dict[str, str] = {}  # upstream serves the exact same id
TEST_MODEL = "glm-5.3"
# PRIMARY tier: this standalone official-account relay wins glm-5.3 traffic;
# the intern-discovery tier (p40) is the automatic failover below it.
PRIORITY = 50
WEIGHT = 5
AUTO_BAN = 1
# glm-5.3 fallback tier — must not win while the primary is healthy
FALLBACK_TIER = (140, 141, 142, 143, 146)


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
    destination = backup_dir / f"new-api-before-ss2a-{stamp}.db"
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


def gateway_chat(smoke, gateway_key: str, model: str, max_tokens: int = 400):
    """One gateway chat; returns (status, usage, attribution_channel_id).

    max_tokens is intentionally >32: glm-5.3 is a reasoning model and a tiny
    budget would return 200 with empty content (see canary note in the docstring).
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
    concurrency/rate limits on these freebie relays; 5xx/524 = upstream blips.
    The final status is still asserted strictly by the caller.
    """
    last = None
    for i in range(1, attempts + 1):
        last = gateway_chat(smoke, gateway_key, model)
        status = last[0]
        if status == 200:
            return last
        if status in (404, 408, 429, 500, 502, 503, 504, 524) and i < attempts:
            print(f"    retry {model}: HTTP {status} (attempt {i}/{attempts})")
            time.sleep(pause)
            continue
        return last
    return last


def channel_status(channel_id: int):
    row = db_one("SELECT status FROM channels WHERE id = ?", (channel_id,))
    return int(row[0]) if row else None


def primary_serves(smoke, headers, channel_id: int, model: str) -> bool:
    """Live admin test on one tier channel (only called when status==1).

    Bounded retries absorb transient classes. Then:
    True  = HTTP 200 + success.
    False = definitive not-serving signature, or a transient-class failure
            (429/5xx/524/concurrency) that persists after retries — routing
            would fail over in exactly the same window.
    Anything else raises — unknown state must not silently flip the assert.
    """
    dead = ("无可用渠道", "Budget pool quota", "No available accounts",
            "not supported by any configured account")
    transient = ("status code 408", "status code 429", "status code 500",
                 "status code 502", "status code 503", "status code 504",
                 "temporarily unavailable", "concurrency limit exceeded")
    for attempt in range(1, 4):
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={model}",
            headers=headers,
            timeout=130,
        )
        if status == 200 and isinstance(body, dict) and body.get("success") is True:
            return True
        text = ""
        if isinstance(body, dict):
            text = f"{body.get('message') or ''} {body.get('error_code') or ''}"
        if any(sig in text for sig in dead):
            return False
        if any(sig in text.lower() for sig in transient):
            if attempt < 3:
                print(f"    primary ch{channel_id} {model}: transient failure (attempt {attempt}/3), retrying")
                time.sleep(20)
                continue
            print(f"    primary ch{channel_id} {model}: transient-class failure persists after retries -> not serving")
            return False
        raise RuntimeError(
            f"primary ch{channel_id} probe inconclusive (unknown != dead): "
            f"HTTP {status} body={str(body)[:220]}"
        )
    return False


def tier_live(smoke, headers, model: str) -> int | None:
    """Return the first enabled fallback-tier channel that actually serves, else None.

    Cheapest sufficient liveness signal: the tier is one upstream pool, so the
    first responder stands in for the tier. A tier channel that is enabled but
    upstream-dead is skipped (enabled != serving).
    """
    for channel_id in FALLBACK_TIER:
        if channel_status(channel_id) != 1:
            continue
        if primary_serves(smoke, headers, channel_id, model):
            return channel_id
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    parser.add_argument(
        "--extend",
        action="store_true",
        help="allow pure model-list additions to the existing channel (PUT with key re-supplied)",
    )
    args = parser.parse_args()

    print("plan:")
    print(f"  create: {CHANNEL_NAME} models={len(MODELS)} prio={PRIORITY} w={WEIGHT} auto_ban={AUTO_BAN}")
    print(f"  base: {BASE_URL} (no /v1; NewAPI auto-appends)  mapping: none (exact ids)")
    print(f"  primary: {CHANNEL_NAME} p{PRIORITY}/w{WEIGHT} wins glm-5.3; fallback tier ch{list(FALLBACK_TIER)} (p40)")
    print("  pricing: read-only parity report (never overwrite ratios)")
    print("  POST /api/channel/fix (abilities rebuild)")
    print("  functional: admin channel test + gateway attribution assert (contested stays primary)")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    key = os.environ.get("SS2A_KEY", "").strip()
    if not key:
        raise RuntimeError("SS2A_KEY env var required")

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
            print(f"extend: ch{existing['id']} {CHANNEL_NAME} models {len(stored_models)} -> {len(want_models)}")
        reused_id = int(existing["id"])
        print(f"resume: reusing existing ch{reused_id} {CHANNEL_NAME}")

    backup = online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    if existing is not None:
        new_id = reused_id
        # PUT rebuilds abilities; contract: no status field, key must be re-supplied
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

    if new_id is None:
        raise RuntimeError("channel id unresolved after create/update (unreachable)")

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

    # admin channel test: real upstream call through NewAPI channel machinery
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
        raise RuntimeError(f"channel test failed: {message!r}")

    # glm-5.3: expectation follows the PRIMARY's CURRENT live state.
    # Primary (this channel) live  -> gateway must attribute ch{new_id}.
    # Primary dead, fallback tier live -> attr in the tier (failover as designed).
    # Both dead -> gateway chat itself fails and raises.
    gateway_key = read_gateway_key()
    status, usage, attr = gateway_chat_retry(smoke, gateway_key, TEST_MODEL)
    if status != 200:
        raise RuntimeError(f"gateway {TEST_MODEL} chat failed: HTTP {status}")
    if attr is None:
        raise RuntimeError(f"{TEST_MODEL}: no consumption-log attribution observed")
    if t_ok:
        if attr != new_id:
            raise RuntimeError(
                f"{TEST_MODEL} attributed ch{attr}, expected primary ch{new_id} — "
                "the p50 primary must win glm-5.3 traffic"
            )
        print(f"primary {TEST_MODEL}: 200 usage={usage.get('prompt_tokens')}/{usage.get('completion_tokens')} "
              f"attributed ch{attr} (primary live, wins traffic)")
    else:
        fallback = tier_live(smoke, headers, TEST_MODEL)
        allowed = {new_id} | set(FALLBACK_TIER)
        if attr not in allowed:
            raise RuntimeError(
                f"{TEST_MODEL}: primary ch{new_id} not serving and attributed ch{attr}, "
                f"expected failover in {sorted(allowed)}"
            )
        note = "fallback tier" if attr in FALLBACK_TIER else f"primary ch{attr} recovered mid-request"
        print(f"primary {TEST_MODEL}: 200 attributed ch{attr} ({note}; fallback tier live at ch{fallback})")

    print(f"DONE: ch{new_id} {CHANNEL_NAME} PRIMARY live at p{PRIORITY}/w{WEIGHT}; fallback tier ch{list(FALLBACK_TIER)} intact")
    print("NEXT: run scripts/ops/newapi-local-smoke.py (policy gate; zero new FAILs) + README ledger + runbook docs/ops/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
