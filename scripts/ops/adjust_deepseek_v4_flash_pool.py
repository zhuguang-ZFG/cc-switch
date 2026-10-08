#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adjust deepseek-v4-flash pool in NewAPI:
- ch180 agentrouter -> main (pri 51)
- ch118 seeseed     -> revive (st=1, auto_ban=0, pri 30) as backup
- ch15  sensenova    -> demote (pri 50 -> 40) as 1M-capable backup

Evidence (2026-10-08, live probes via each channel's own key):
- ch180 direct POST /v1/chat/completions max_tokens=131072 -> 200
- ch118 direct POST /v1/chat/completions deepseek-v4-flash -> 200 (was auto-banned on prior ReadTimeout)
- ch15  returns 429 "inference exceeds tpm/rpm limit" and NewAPI passes it through (no failover)

Uses NewAPI admin API (PUT /api/channel/) so the channel cache stays consistent.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()
DB_PATH = HOME / ".new-api-local" / "new-api.db"
BACKUP_DIR = HOME / ".new-api-local" / "backups"
SECRETS_PATH = HOME / ".omp" / "guardian" / "secrets.json"
BASE = "http://127.0.0.1:3002"

CHANGES = {
    15: {"priority": 40},   # sensenova: demote to backup
    118: {"status": 1, "auto_ban": 0, "priority": 30},  # seeseed: revive
    180: {"priority": 51},  # agentrouter: main
}
BACKUP_PREFIX = "new-api-before-deepseek-pool-adjust"


def _redact(text: str) -> str:
    if not text:
        return text
    return re.sub(r"\b(?:sk-|gsk-|pk-|tok-|5jfnxhMjK5E)[A-Za-z0-9_-]{10,}\b", "[REDACTED]", text)


def log(msg: str) -> None:
    print(_redact(msg), flush=True)


from typing import NoReturn


def fatal(msg: str) -> NoReturn:
    log(f"ERROR: {msg}")
    sys.exit(1)


def load_admin_token() -> str:
    try:
        data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        fatal(f"cannot read {SECRETS_PATH}: {exc}")
    token = data.get("newapi_token") or data.get("newapi_user")
    if not token:
        fatal("newapi_token/newapi_user missing in secrets.json")
    return token


def api_call(token: str, method: str, path: str, payload: dict | None = None, timeout: int = 30) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def backup_db() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"{BACKUP_PREFIX}-{ts}.db"
    shutil.copy2(DB_PATH, dest)
    return dest


def get_channel(token: str, cid: int) -> dict:
    body = api_call(token, "GET", f"/api/channel/{cid}")
    data = body.get("data")
    if not isinstance(data, dict):
        fatal(f"channel {cid}: unexpected GET response {body}")
    return data


def update_channel(token: str, channel: dict) -> None:
    cid = channel.get("id")
    resp = api_call(token, "PUT", f"/api/channel/{cid}", {"mode": "single", "channel": channel})
    if not resp.get("success"):
        fatal(f"PUT channel {cid} failed: {resp}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print plan only, do not write")
    args = parser.parse_args()

    token = load_admin_token()
    backup = backup_db()
    log(f"backup: {backup.name}")

    for cid, want in CHANGES.items():
        cur = get_channel(token, cid)
        before = {k: cur.get(k) for k in ("status", "auto_ban", "priority", "weight", "name")}
        log(f"ch{cid} {before['name']}: before {before}, applying {want}")
        if args.dry_run:
            continue
        cur.update(want)
        # drop read-only fields NewAPI rejects on write
        for k in ("created_time", "created_at", "updated_time", "updated_at", "channel_info", "test_time", "response_time", "used_quota", "model_mapping_raw"):
            cur.pop(k, None)
        update_channel(token, cur)
        after = get_channel(token, cid)
        log(f"ch{cid} after: status={after.get('status')} auto_ban={after.get('auto_ban')} priority={after.get('priority')}")

    log("DONE. Channel cache syncs within ~1 min; verify with /v1/chat/completions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())