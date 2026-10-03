#!/usr/bin/env python3
"""Onboard StepFun Step Plan (step-3.7-flash + step-router-v1) into the local NewAPI fork (2026-10-03).

Background
----------
User supplied a Step Plan key and base ``https://api.stepfun.com/step_plan/v1``
(historical ch36 stepfun-step-plan from 2026-07-31 was deleted; no local
remnants — new channel required). Direct probes with the user key (key handled
via env var only — never written to the repo, logs, or docs; on --apply it is
POSTed to NewAPI and persisted as the channel credential in the local NewAPI
DB, which is the SSOT for channel keys):

- ``GET /step_plan/v1/models`` -> catalog incl. ``step-3.7-flash`` (262144
  input, vision, reasoning, chat/messages/responses, effort low/medium/high),
  ``step-router-v1`` (262144, reasoning; upstream auto-router), plus
  stepaudio-2.5 chat/tts/asr (audio models NOT onboarded — no consumer);
- ``step-3.7-flash`` chat 200 (STEP_OK, finish=stop, reasoning_content present).

Subscription channel (Step Plan consumes the user's own plan quota).
This script does NOT modify pricing: runbook
docs/patches/stepfun-step-plan-newapi-2026-07-31.md mandates
ModelRatio/CompletionRatio = 0.5/2 for step-router-v1 (verified read-only,
already in DB); step-3.7-flash keeps its existing ratio (official price
open item). A disabled ability for step-3.7-flash survives on ch110
(yjs-free, status=2): channel/fix only sweeps dead-channel abilities, so
it remains — harmless while ch110 stays disabled (collision: ch110 prio=6
would outrank the new channel's prio=0 if ever re-enabled; see runbook).

Usage: ``STEPFUN_KEY=sk-... python3 add_stepfun_step_plan_channel.py [--apply]``
Key comes from the env var only; it is never printed, logged, or written
to the repo. Dry-run by default (no key required).
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
# (lesson from docs/patches/stepfun-step-plan-newapi-2026-07-31.md)
BASE_URL = "https://api.stepfun.com/step_plan"
CHANNEL_NAME = "stepfun-step-plan"
MODELS = ["step-3.7-flash", "step-router-v1"]
PRIMARY_MODEL = "step-3.7-flash"
PRIORITY = 0
WEIGHT = 2


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load smoke helper: {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def db_one(sql, params=()):
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as c:
        return c.execute(sql, params).fetchone()


def db_all(sql, params=()):
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as c:
        return c.execute(sql, params).fetchall()


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        f"new-api-before-stepfun-step-plan-{time.strftime('%Y%m%d-%H%M%S')}.db"
    )
    source = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        target = sqlite3.connect(destination, timeout=30)
        try:
            source.backup(target)
            result = target.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError(f"backup integrity check failed: {result}")
        finally:
            target.close()
    finally:
        source.close()
    return destination


def fetch_channels(smoke, headers):
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=200", headers=headers
    )
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"channel list failed: HTTP {status}")
    items = body.get("data") or []
    if isinstance(items, dict):
        items = items.get("items") or []
    return [i for i in items if isinstance(i, dict)]


def read_gateway_key() -> str:
    """OMP zg-newapi apiKey from live models.yml (never printed)."""
    import re

    text = (Path.home() / ".omp" / "agent" / "models.yml").read_text(encoding="utf-8")
    match = re.search(r"^  zg-newapi:\n(?:    .*\n)*?    apiKey:\s*(\S+)", text, flags=re.M)
    if not match:
        raise RuntimeError("zg-newapi apiKey not found in live models.yml")
    return match.group(1)


def wait_abilities(channel_id: int, model: str, seconds: int = 90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        row = db_one(
            "SELECT [group], enabled, priority, weight FROM abilities WHERE channel_id = ? AND model = ?",
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
    print(f"  base={BASE_URL} (no /v1; NewAPI auto-appends)")
    print("  pricing: read-only verify (step-router-v1 must be 0.5/2 per runbook; nothing overwritten)")
    print("  POST /api/channel/fix (dead-channel sweep; disabled ch110 step-3.7-flash ability remains, documented)")
    print("  functional: gateway chat step-3.7-flash (hard) + step-router-v1 (soft) + log attribution")

    if not args.apply:
        print("dry-run: no changes made")
        return 0

    key = os.environ.get("STEPFUN_KEY", "").strip()
    if not key:
        raise RuntimeError("STEPFUN_KEY env var required")

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    channels = fetch_channels(smoke, headers)
    by_name = {str(c.get("name")): c for c in channels}
    if CHANNEL_NAME in by_name:
        raise RuntimeError(f"channel name already exists: {CHANNEL_NAME} (ch{by_name[CHANNEL_NAME]['id']})")
    live = db_one("SELECT channel_id FROM abilities WHERE model = ? AND enabled = 1", (PRIMARY_MODEL,))
    if live:
        raise RuntimeError(f"{PRIMARY_MODEL} already has an enabled ability: ch{live[0]}")

    backup = online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    # 1. create channel
    payload = {
        "name": CHANNEL_NAME,
        "type": 1,
        "base_url": BASE_URL,
        "key": key,
        "models": ",".join(MODELS),
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

    # 2. pricing: read-only verification — never overwrite ratios.
    # Runbook docs/patches/stepfun-step-plan-newapi-2026-07-31.md mandates
    # step-router-v1 ModelRatio=0.5 / CompletionRatio=2; both already in DB.
    row = db_one("SELECT value FROM options WHERE key = 'ModelRatio'")
    mr = json.loads(row[0]) if row and row[0] else {}
    row = db_one("SELECT value FROM options WHERE key = 'CompletionRatio'")
    cr = json.loads(row[0]) if row and row[0] else {}
    if mr.get("step-router-v1") != 0.5 or cr.get("step-router-v1") != 2:
        raise RuntimeError(
            f"step-router-v1 pricing drift: ModelRatio={mr.get('step-router-v1')} "
            f"CompletionRatio={cr.get('step-router-v1')} (runbook requires 0.5/2)"
        )
    print(
        f"pricing verified: step-router-v1 = {mr['step-router-v1']}/{cr['step-router-v1']} (runbook); "
        f"step-3.7-flash = {mr.get('step-3.7-flash')}/{cr.get('step-3.7-flash')} "
        f"(existing value kept; official price open item - platform.stepfun.com pricing page)"
    )

    # 3. fix + verify abilities for both models
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
    step_abs = db_all("SELECT channel_id, model, enabled FROM abilities WHERE model LIKE 'step%'")
    stray = [r for r in step_abs if int(r[0]) != new_id and int(r[2]) == 1]
    if stray:
        raise RuntimeError(f"stray enabled step* abilities after fix: {stray}")
    print(f"verify ok: abilities ch{new_id} x{len(MODELS)} (default,1,{PRIORITY},{WEIGHT}); "
          f"step* ability rows now: {[(int(r[0]), r[1], int(r[2])) for r in step_abs]}")

    # 4. functional: gateway chat + log attribution (hard for flash, soft for router)
    gateway_key = read_gateway_key()
    # The gateway ability cache lags channel/fix: a chat fired immediately
    # after abilities appear can route nowhere (HTTP 200, no log row —
    # observed 2026-10-03, ch144). Retry chat+attribution with backoff.
    usage = {}
    attr = None
    for attempt in range(6):
        mark = time.time()
        status, body = smoke.http_json(
            f"{GATEWAY_BASE}/v1/chat/completions",
            method="POST",
            body={
                "model": PRIMARY_MODEL,
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "max_tokens": 512,
            },
            headers={"Authorization": f"Bearer {gateway_key}"},
            timeout=180,
        )
        if status != 200:
            raise RuntimeError(f"gateway chat probe failed: HTTP {status}: {json.dumps(body)[:200]}")
        usage = body.get("usage") or {}
        with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as c:
            row_log = c.execute(
                "SELECT channel_id FROM logs WHERE model_name = ? AND created_at > ? AND type = 2 "
                "ORDER BY id DESC LIMIT 1",
                (PRIMARY_MODEL, mark),
            ).fetchone()
        attr = int(row_log[0]) if row_log else None
        if attr == new_id:
            break
        if attempt < 5:
            time.sleep(5)
    if attr != new_id:
        raise RuntimeError(f"attribution mismatch after retries: logs ch{attr}, expected ch{new_id}")
    print(
        f"functional OK: {PRIMARY_MODEL} gateway chat 200 usage={usage.get('prompt_tokens')}/"
        f"{usage.get('completion_tokens')} attributed ch{attr}"
    )

    r_status, r_body = smoke.http_json(
        f"{GATEWAY_BASE}/v1/chat/completions",
        method="POST",
        body={
            "model": "step-router-v1",
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": 512,
        },
        headers={"Authorization": f"Bearer {gateway_key}"},
        timeout=180,
    )
    r_usage = (r_body.get("usage") or {}) if isinstance(r_body, dict) else {}
    print(f"soft probe: step-router-v1 HTTP {r_status} usage={r_usage.get('prompt_tokens')}/"
          f"{r_usage.get('completion_tokens')} (upstream auto-router; non-gating)")

    print(f"DONE: ch{new_id} {CHANNEL_NAME} live; models: {','.join(MODELS)}")
    print("NEXT: register models in ~/.omp/agent/models.yml, then vision chain edit, then OMP restart")
    return 0


if __name__ == "__main__":
    sys.exit(main())
