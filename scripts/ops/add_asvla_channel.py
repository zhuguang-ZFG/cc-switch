#!/usr/bin/env python3
"""Onboard asvla.bbqwq.com (claude满血全系列) as a backup Claude channel in local NewAPI (2026-10-07).

User-provided key via ASVLA_KEY env only (never printed, never persisted).
Upstream self-identifies as "sub2api" + "Astra由BBcloud提供"; full Anthropic
protocol (msg_* ids). /v1/messages accepts x-api-key AND Bearer; anthropic SSE
(message_start/message_stop) verified; conformance canary fully clean (0 issues
incl. nested tool_choice + stream; no prompt injection; per-user cache).

Live probe 2026-10-07 (direct, 2.2-5s):
  claude-opus-5 / opus-5-5 / sonnet-5 / fable-5 / fable-5-1 / opus-4-8 /
  opus-4-6 / opus-4-7 / sonnet-4-6 / haiku-4-5 all 200 (chat + messages).
  Excluded: claude-haiku-4-5-20251001 (403 server_error); the catalog's gpt-*
  ids are model_not_found (luna/sol/terra unreachable — re-probe condition).

Pool posture at onboarding time (HIGHER priority dispatches first):
- opus-5: ch9 (p52) upstream-dead ("No available accounts"); ch148 (p-10) serves.
- opus-5-5 / fable-5-1: ch3 (p54) serves. fable-5: only ch9 (dead at probe time).
- opus-4-8: ch95 (p50) serves. haiku-4-5: ch69/ch68 serve.
- opus-4-6 / opus-4-7 / sonnet-4-6 / sonnet-5: ZERO carriers anywhere — this
  channel becomes their sole source.
This channel sits at p-20/w1 (below ch148 p-10, ch3 p54, ch9 p52, ch95 p50) —
contested traffic must NOT migrate while any listed higher-tier carrier serves.

Precedent: add_hubway_channel.py / add_grok_heavy_channel.py.
- Pricing: read-only parity report, never overwrite ratios.
- Idempotent resume with drift detection; DB online backup before any write.
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
BASE_URL = "https://asvla.bbqwq.com"
CHANNEL_NAME = "asvla-claude"
CHANNEL_TYPE = 14  # Anthropic upstream (/v1/messages); mirrors ch9/ch3/123
MODELS = [
    "claude-fable-5",
    "claude-fable-5-1",
    "claude-haiku-4-5",
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-opus-5",
    "claude-opus-5-5",
    "claude-sonnet-4-6",
    "claude-sonnet-5",
]
MODEL_MAPPING: dict[str, str] = {}  # upstream serves exact same ids
# fastest key model (2.6s) and the OMP-relevant id exercising the real path
TEST_MODEL = "claude-opus-5"
# Backup tier: enabled primaries live at p-10..p54; the backup MUST sit strictly
# below every enabled carrier or it silently becomes primary.
PRIORITY = -20
WEIGHT = 1
# models with pre-existing enabled higher-tier carriers; traffic must NOT
# migrate while ANY listed carrier serves (probed live at runtime)
CONTESTED = {
    "claude-fable-5": (9,),
    "claude-fable-5-1": (3,),
    "claude-haiku-4-5": (69, 68),
    "claude-opus-4-8": (95,),
    "claude-opus-5": (9, 148),
    "claude-opus-5-5": (3,),
}
# zero carriers anywhere; this channel must serve them end-to-end
FREE = ["claude-opus-4-6", "claude-opus-4-7", "claude-sonnet-4-6", "claude-sonnet-5"]


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
    destination = backup_dir / f"new-api-before-asvla-claude-{stamp}.db"
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
    """Live admin test on the declared primary (only called when status==1).

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
    print(f"  contested pools: {json.dumps(CONTESTED)} -> probe each listed carrier live; must NOT win while one serves")
    print(f"  free models (zero carriers): {FREE} -> must serve end-to-end from here")
    print("  pricing: read-only parity report (never overwrite ratios)")
    print("  POST /api/channel/fix (abilities rebuild)")
    print("  functional: admin channel test + gateway chats (contested stays primary; unique models serve)")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    key = os.environ.get("ASVLA_KEY", "").strip()
    if not key:
        raise RuntimeError("ASVLA_KEY env var required")

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
                "type": CHANNEL_TYPE,
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
            "type": CHANNEL_TYPE,
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

    gateway_key = read_gateway_key()

    # contested models: the expectation follows the CURRENT live state of every
    # listed higher-tier carrier (admin-test probe). Serving primary -> backup
    # must NOT win; all listed dead -> takeover here is the designed behavior.
    for model, primaries in CONTESTED.items():
        serving = [
            p for p in primaries
            if channel_status(p) == 1 and primary_serves(smoke, headers, p, model)
        ]
        status, usage, attr = gateway_chat_retry(smoke, gateway_key, model)
        if status != 200:
            raise RuntimeError(f"gateway {model} chat failed: HTTP {status}")
        if attr is None:
            raise RuntimeError(f"{model}: no consumption-log attribution observed")
        if serving:
            if attr == new_id:
                raise RuntimeError(
                    f"{model} attributed ch{attr} (backup) while higher-tier {serving} serves — "
                    "backup must not win contested traffic"
                )
            print(f"contested {model}: 200 usage={usage.get('prompt_tokens')}/{usage.get('completion_tokens')} attributed ch{attr} (higher-tier {serving} keeps traffic)")
        else:
            # none serving at probe time: takeover (attr==new_id) or a listed
            # primary recovered mid-request (attr in primaries) are both correct
            allowed = {new_id, *primaries}
            if attr not in allowed:
                raise RuntimeError(
                    f"{model}: no higher-tier carrier serving but attributed ch{attr}, "
                    f"expected takeover ch{new_id} or a listed primary {primaries}"
                )
            note = "takeover" if attr == new_id else f"primary ch{attr} recovered mid-request"
            print(f"contested {model}: 200 attributed ch{attr} ({note})")

    # zero-carrier models: this channel is the only source; must serve end-to-end
    for model in FREE:
        status, usage, attr = gateway_chat_retry(smoke, gateway_key, model)
        if status != 200:
            raise RuntimeError(f"gateway {model} chat failed: HTTP {status}")
        if attr != new_id:
            raise RuntimeError(f"{model} attributed ch{attr}, expected sole carrier ch{new_id}")
        print(f"serves {model}: 200 usage={usage.get('prompt_tokens')}/{usage.get('completion_tokens')} attributed ch{attr} (sole carrier, as designed)")

    print(f"DONE: ch{new_id} {CHANNEL_NAME} live; contested={len(CONTESTED)} in backup posture, free={len(FREE)} sole-carrier models")
    print("NEXT: run scripts/ops/newapi-local-smoke.py (policy gate; zero new FAILs) + README ledger + runbook docs/ops/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
