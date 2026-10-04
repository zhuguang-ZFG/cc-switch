#!/usr/bin/env python3
"""Onboard relay.obitmc.com (shared free relay) as a backup-only channel in local NewAPI (2026-10-04).

Thin adaptation of add_budsin_apichat_channel.py (ch148 precedent, r7 contract).

Upstream facts (read-only probes, 2026-10-04):
- OpenAI chat format ONLY (/v1/messages 404 "No such endpoint on this relay").
- Cloudflare 1010 blocks python-urllib default UA; curl default UA 200, browser UA 200.
  NewAPI Go client is expected to pass; the admin channel test below is the real-path gate.
- unsloth/Qwen3.8-27B-GGUF: real completion, reasoning model (reasoning_content);
  max_tokens too small -> content=null (budget eaten by reasoning). Probe used >=48.
- internal/qwen3.8-27b-nie: cold-start 503 first_chunk_timeout, warm 200 (3.6s). NOT onboarded
  (same relay, same class; v1 keeps one canonical mapping to halve guardian probe load).
- Forum post states a concurrency limit (untested, not load-tested by policy).

Pool facts (abilities read 2026-10-04): qwen3-8-27b enabled legs ch124 p50, ch88 p49,
ch140-143 p40 (all w1); disabled ch112/ch113. In-service minimum priority = 40 ->
backup posture p20/w1 (<=50 policy, strictly below minimum).

Design: exact-id canonical qwen3-8-27b, model_mapping to upstream GGUF id.
OMP models.yml zero change (transparent redundancy behind canonical id).
Semantic divergence: canonical declares reasoning:false (ch88 leg) while this leg emits
reasoning_content; acceptable for backup-only posture (content still completes given
normal OMP max_tokens), documented in runbook risks.

Key via OBITMC_KEY env (never printed). Dry-run default; --apply to mutate.
Idempotent resume: existing channel with identical config is reused; drift -> refuse.
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
BASE_URL = "https://relay.obitmc.com"
CHANNEL_NAME = "obitmc-relay"
MODELS = ["qwen3-8-27b"]
MODEL_MAPPING = {"qwen3-8-27b": "unsloth/Qwen3.8-27B-GGUF"}
PRIMARY_MODEL = "qwen3-8-27b"  # test_model: canonical id (mapped upstream)
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
    destination = backup_dir / f"new-api-before-obitmc-relay-{stamp}.db"
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
            "SELECT \"group\", enabled, priority, weight FROM abilities WHERE channel_id = ? AND model = ?",
            (channel_id, model),
        )
        if row:
            return row
        time.sleep(3)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    args = parser.parse_args()

    print("plan:")
    print(f"  create: {CHANNEL_NAME} models={','.join(MODELS)} prio={PRIORITY} w={WEIGHT}")
    print(f"  mapping: {json.dumps(MODEL_MAPPING)}  base={BASE_URL} (no /v1; NewAPI auto-appends)")
    print("  pricing: read-only verify (parity report, never overwrite)")
    print("  POST /api/channel/fix (dead-channel sweep; abilities rebuild)")
    print("  functional: admin channel test + gateway chain-health chat (attribution stays on primaries)")
    print("  posture: backup-only (p20/w1 below in-service minimum p40)")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    key = os.environ.get("OBITMC_KEY", "").strip()
    if not key:
        raise RuntimeError("OBITMC_KEY env var required")

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    channels = fetch_channels(smoke, headers)
    by_name = {str(c.get("name")): c for c in channels}
    existing = by_name.get(CHANNEL_NAME)
    if existing is not None:
        # idempotent resume: reuse only when stored config matches this contract exactly
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

    # 4. admin channel test (real upstream call through NewAPI channel machinery;
    #    also the definitive CF-vs-Go-client gate)
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

    # 5. gateway chain-health: qwen3-8-27b chat must 200; attribution expected on an
    #    existing higher-priority leg, NOT the new p20 backup (asserted, not hidden).
    #    max_tokens generous: reasoning legs can burn budget before content.
    gateway_key = read_gateway_key()
    mark = time.time()
    status, body = smoke.http_json(
        f"{GATEWAY_BASE}/v1/chat/completions",
        method="POST",
        body={
            "model": "qwen3-8-27b",
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": 64,
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
            ("qwen3-8-27b", mark),
        )
        if row_log:
            attr = int(row_log[0])
            break
        time.sleep(5)
    if attr == new_id:
        raise RuntimeError("unexpected: backup channel served qwen3-8-27b while primaries are enabled")
    print(f"chain-health OK: qwen3-8-27b 200 usage={usage.get('prompt_tokens')}/{usage.get('completion_tokens')} "
          f"attributed ch{attr} (existing primary, as designed)")

    print(f"DONE: ch{new_id} {CHANNEL_NAME} live as backup; models: {','.join(MODELS)}")
    print("NEXT: run scripts/ops/newapi-local-smoke.py (policy gate) + guardian.log observation point")
    return 0


if __name__ == "__main__":
    sys.exit(main())
