#!/usr/bin/env python3
"""Restore gpt-5.6-luna on the OpenCode Go plan (2026-10-02, second pass).

Background
----------
OMP models.yml still carries ``gpt-5.6-luna`` (display name referenced the
old ch106 ``opencode-go-luna``), but ch106 no longer exists in the local
NewAPI fork and every other channel listing ``gpt-5.6-luna`` (ch82/94/95/107)
is disabled with abilities enabled=0. Result: distributor 503
``No available channel for model gpt-5.6-luna under group default``.

Direct upstream probes with the rotated Go key (same key as ch130..ch133,
read from the DB SSOT, never printed) show:

- ``/v1/models`` still lists ``gpt-5.6-luna`` (36-model Go catalog);
- chat/completions -> 400 ``ModelProtocolUnsupported`` (same shape as
  gpt-6-luna / muse-spark-1.3-contributor: responses-only);
- ``/v1/responses`` -> 200 status=completed.

So this script recreates a dedicated channel mirroring the 2026-10-02
rotation posture (ch131/132/133): type=1, base_url and header_override
(User-Agent + x-opencode-session) cloned from ch130, priority 0, weight 2,
ModelRatio=0 (Go flat subscription, zero marginal cost).

Usage: dry-run by default; ``--apply`` executes. Key is read from the local
NewAPI DB (ch130), never from argv/env/logs.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
DB_PATH = Path.home() / ".new-api-local" / "new-api.db"
GATEWAY_BASE = "http://127.0.0.1:3002"

MODEL = "gpt-5.6-luna"
CHANNEL_NAME = "opencode-go-gpt-5.6-luna"
KEY_DONOR = 130  # opencode-go-space-bunny-free: key + header_override + base_url donor; NOT modified
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


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        f"new-api-before-opencode-go-gpt56luna-{time.strftime('%Y%m%d-%H%M%S')}.db"
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
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=200",
        headers=headers,
        timeout=30,
    )
    if status != 200:
        raise RuntimeError(f"channel list failed: HTTP {status}")
    items = body.get("data", {}).get("items") or body.get("data") or []
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

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    channels = fetch_channels(smoke, headers)
    by_id = {int(c["id"]): c for c in channels}
    by_name = {str(c.get("name")): c for c in channels}

    # preflight
    if KEY_DONOR not in by_id:
        raise RuntimeError(f"ch{KEY_DONOR} key donor missing")
    donor = by_id[KEY_DONOR]
    header_override = donor.get("header_override")
    if not header_override or "x-opencode-session" not in str(header_override):
        raise RuntimeError(f"ch{KEY_DONOR} header_override missing session header; refusing to clone")
    base_url = str(donor.get("base_url") or "").rstrip("/")
    if CHANNEL_NAME in by_name:
        raise RuntimeError(f"channel name already exists: {CHANNEL_NAME} (ch{by_name[CHANNEL_NAME]['id']})")
    donor_row = db_one("SELECT key FROM channels WHERE id = ?", (KEY_DONOR,))
    if not donor_row or not donor_row[0]:
        raise RuntimeError(f"ch{KEY_DONOR} key not readable from DB")
    live = db_one(
        "SELECT channel_id FROM abilities WHERE model = ? AND enabled = 1", (MODEL,)
    )
    if live:
        raise RuntimeError(f"{MODEL} already has an enabled ability: ch{live[0]}")

    print("plan:")
    print(f"  create: {CHANNEL_NAME} models={MODEL} prio={PRIORITY} w={WEIGHT}")
    print(f"  key/header_override/base_url cloned from ch{KEY_DONOR} (donor not modified)")
    print(f"  ModelRatio[{MODEL}] = 0; POST /api/channel/fix")
    print(f"  functional: gateway /v1/responses (responses-only model) + log attribution")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    # 1. create channel (key from DB SSOT, never printed)
    payload = {
        "name": CHANNEL_NAME,
        "type": 1,
        "base_url": base_url,
        "key": donor_row[0],
        "models": MODEL,
        "group": "default",
        "priority": PRIORITY,
        "weight": WEIGHT,
        "auto_ban": 1,
        "test_model": MODEL,
        "header_override": header_override,
    }
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/",
        method="POST",
        body={"mode": "single", "channel": payload},
        headers=headers,
        timeout=30,
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

    # 2. ModelRatio
    row = db_one("SELECT value FROM options WHERE key = 'ModelRatio'")
    ratios = json.loads(row[0]) if row and row[0] else {}
    ratios[MODEL] = 0
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/option/",
        method="PUT",
        body={"key": "ModelRatio", "value": json.dumps(ratios, separators=(",", ":"), sort_keys=True)},
        headers=headers,
        timeout=30,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"ModelRatio update failed: HTTP {status} message={message!r}")
    print(f"ModelRatio[{MODEL}] = 0")

    # 3. fix + verify abilities
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/fix", method="POST", body={}, headers=headers, timeout=60
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"channel fix failed: HTTP {status} message={message!r}")
    row_ab = wait_abilities(new_id, MODEL)
    if not row_ab or tuple(row_ab) != ("default", 1, PRIORITY, WEIGHT):
        raise RuntimeError(f"abilities ch{new_id}/{MODEL} = {row_ab} (absent or wrong shape after 90s)")
    ratios_rb = json.loads(db_one("SELECT value FROM options WHERE key = 'ModelRatio'")[0])
    if ratios_rb.get(MODEL) != 0:
        raise RuntimeError(f"ModelRatio[{MODEL}] readback = {ratios_rb.get(MODEL)!r}")
    print(f"verify ok: abilities ch{new_id} (default,1,{PRIORITY},{WEIGHT}), ratio 0")

    # 4. functional: gateway /v1/responses + log attribution
    gateway_key = read_gateway_key()
    mark = time.time()
    status, body = smoke.http_json(
        f"{GATEWAY_BASE}/v1/responses",
        method="POST",
        body={"model": MODEL, "input": "Reply with exactly: OK", "max_output_tokens": 64},
        headers={"Authorization": f"Bearer {gateway_key}"},
        timeout=180,
    )
    if status != 200:
        raise RuntimeError(f"gateway responses probe failed: HTTP {status}: {json.dumps(body)[:200]}")
    usage = body.get("usage") or {}
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as c:
        row_log = c.execute(
            "SELECT channel_id FROM logs WHERE model_name = ? AND created_at > ? AND type = 2 "
            "ORDER BY id DESC LIMIT 1",
            (MODEL, mark),
        ).fetchone()
    attr = int(row_log[0]) if row_log else None
    if attr != new_id:
        raise RuntimeError(f"attribution mismatch: logs ch{attr}, expected ch{new_id}")
    print(
        f"functional OK: gateway /v1/responses 200 status={body.get('status')} "
        f"usage={usage.get('input_tokens')}/{usage.get('output_tokens')} attributed ch{attr}"
    )
    print(f"DONE: ch{new_id} {CHANNEL_NAME} live; update models.yml name to ch{new_id} + api: openai-responses")
    return 0


if __name__ == "__main__":
    sys.exit(main())
