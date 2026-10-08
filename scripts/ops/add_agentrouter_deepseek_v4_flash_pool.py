#!/usr/bin/env python3
"""Aggregate agentrouter's `deepseek-v4-flash` into NewAPI as a second source.

The agentrouter service (Tailscale, http://100.83.32.95:8788/v1) exposes
`deepseek-v4-flash` in its live model catalog (probed 200). NewAPI's existing
agentrouter-named channels (86/127/134-136) point at agentrouter.org /
ps.air-outer.com whose keys are dead (401 on /v1/models), so they cannot carry
the model. This script creates a dedicated single-model channel to the live
Tailscale endpoint using the key already present in OMP models.yml
(`agentrouter` provider apiKey; never printed).

Primary source ch15 (sensenova-token, p50) is unchanged; the new channel is a
fallback at p40/w5, mirroring add_agentrouter_glm53_pool.py. NewAPI's OpenAI
channel type appends `/v1` itself, so base_url is the bare root (no /v1);
the agentrouter key still comes from models.yml whose provider baseUrl has
the `/v1` suffix.

Runbook:
- key source  : `agentrouter` provider apiKey in ~/.omp/agent/models.yml
- base url    : http://100.83.32.95:8788  (NewAPI appends /v1; do not add /v1)
- new channel : `agentrouter-deepseek-v4-flash` (type 1 OpenAI, p40/w5)
- primary     : ch15 sensenova-token (p50/w1) -- unchanged
- fallback    : agentrouter-deepseek-v4-flash (p40/w5)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import NoReturn

HOME = Path.home()
DB_PATH = HOME / ".new-api-local" / "new-api.db"
BACKUP_DIR = HOME / ".new-api-local" / "backups"
SECRETS_PATH = HOME / ".omp" / "guardian" / "secrets.json"
MODELS_YAML_PATH = HOME / ".omp" / "agent" / "models.yml"
BASE = "http://127.0.0.1:3002"

MODEL = "deepseek-v4-flash"
AGENTROUTER_BASE_URL = "http://100.83.32.95:8788"
NEW_CHANNEL_NAME = "agentrouter-deepseek-v4-flash"
PRIORITY = 40
WEIGHT = 5
BACKUP_PREFIX = "new-api-before-agentrouter-deepseek-v4-flash"
PROBE_MAX_TOKENS = 64


def _redact(text: str) -> str:
    """Mask any API-key-shaped token; never hardcode key literals here."""
    if not text:
        return text
    return re.sub(r"\b(?:sk-|gsk-|pk-|tok-|5jfnxhMjK5E)[A-Za-z0-9_-]{10,}\b", "[REDACTED]", text)


def log(msg: str) -> None:
    print(_redact(msg), flush=True)


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
    url = f"{BASE}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        fatal(f"HTTP {exc.code} on {path}: {body}")


def db_conn(readonly: bool = True) -> sqlite3.Connection:
    mode = "mode=ro" if readonly else "mode=rwc"
    conn = sqlite3.connect(f"file:{DB_PATH}?{mode}", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def backup_db() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"{BACKUP_PREFIX}-{ts}.db"
    shutil.copy2(DB_PATH, dest)
    return dest


def get_channel_row(channel_id: int) -> sqlite3.Row:
    conn = db_conn(True)
    row = conn.execute("SELECT * FROM channels WHERE id=?", (channel_id,)).fetchone()
    conn.close()
    if not row:
        fatal(f"channel {channel_id} not found")
    return row


def find_channel_by_name(name: str) -> int | None:
    conn = db_conn(True)
    row = conn.execute("SELECT id FROM channels WHERE name=?", (name,)).fetchone()
    conn.close()
    return row["id"] if row else None


def new_channel_payload(base_url: str, key: str) -> dict:
    return {
        "type": 1,
        "name": NEW_CHANNEL_NAME,
        "base_url": base_url,
        "key": key,
        "models": MODEL,
        "model_mapping": "",
        "priority": PRIORITY,
        "weight": WEIGHT,
        "auto_ban": 1,
        "status": 1,
    }


def create_channel(token: str, payload: dict) -> int:
    body = api_call(token, "POST", "/api/channel/", {"mode": "single", "channel": payload})
    new_id = (body.get("data") or {}).get("id")
    if new_id:
        return int(new_id)
    cid = find_channel_by_name(NEW_CHANNEL_NAME)
    if not cid:
        fatal("channel created but id not returned and name lookup failed")
    return cid


def abilities_rows(model: str) -> list[tuple[int, int, int, int]]:
    conn = db_conn(True)
    rows = conn.execute(
        "SELECT channel_id, enabled, priority, weight FROM abilities WHERE model=?",
        (model,),
    ).fetchall()
    conn.close()
    return [(r["channel_id"], r["enabled"], r["priority"], r["weight"]) for r in rows]


def delete_channel_full(token: str, channel_id: int) -> None:
    try:
        api_call(token, "DELETE", f"/api/channel/{channel_id}")
    except Exception as exc:
        log(f"cleanup DELETE failed (will try direct DB): {exc}")
        conn = db_conn(False)
        conn.execute("DELETE FROM abilities WHERE channel_id=?", (channel_id,))
        conn.execute("DELETE FROM channels WHERE id=?", (channel_id,))
        conn.commit()
        conn.close()
    log(f"cleaned up channel {channel_id}")


def verify_channel(channel_id: int) -> None:
    row = get_channel_row(channel_id)
    if row["name"] != NEW_CHANNEL_NAME:
        fatal(f"name mismatch: {row['name']}")
    if row["status"] != 1:
        fatal(f"channel not enabled: status={row['status']}")
    if row["priority"] != PRIORITY or row["weight"] != WEIGHT:
        fatal(f"priority/weight mismatch: {row['priority']}/{row['weight']}")
    rows = abilities_rows(MODEL)
    ability = next((r for r in rows if r[0] == channel_id), None)
    if not ability or ability[1] != 1:
        fatal(f"ability not enabled for {MODEL}: {ability}")
    log(f"verified ch{channel_id}: name={row['name']} status={row['status']} "
        f"priority={row['priority']} weight={row['weight']} ability={ability}")


def read_agentrouter_key() -> str:
    """Read the agentrouter provider apiKey from live models.yml (never printed)."""
    text = MODELS_YAML_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"agentrouter:\s*\n\s+baseUrl: http://100\.83\.32\.95:8788/v1\s*\n\s+api: openai-completions\s*\n\s+apiKey:\s*(\S+)",
        text,
    )
    if not match:
        fatal("cannot find agentrouter apiKey in models.yml")
    return match.group(1)


def channel_test(token: str, channel_id: int) -> bool:
    body = api_call(token, "GET", f"/api/channel/test/{channel_id}", timeout=90)
    ok = body.get("success") is True
    log(f"channel test ch{channel_id}: success={ok} message={body.get('message','')[:120]}")
    return ok


def upstream_probe(key: str) -> bool:
    """Directly call the agentrouter endpoint to prove deepseek-v4-flash answers."""
    payload = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": "Reply with exactly: pong"}],
        "max_tokens": PROBE_MAX_TOKENS,
        "stream": False,
    }).encode()
    req = urllib.request.Request(f"{AGENTROUTER_BASE_URL}/v1/chat/completions", data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            finish = (data.get("choices") or [{}])[0].get("finish_reason")
            echoed = data.get("model")
            log(f"upstream probe {MODEL}: status={resp.status} finish={finish} "
                f"content={content[:40]!r} echoed_model={echoed}")
            return resp.status == 200
    except Exception as exc:
        log(f"upstream probe FAILED: {exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="probe only, do not create channel")
    args = parser.parse_args()

    token = load_admin_token()
    existing_id = find_channel_by_name(NEW_CHANNEL_NAME)
    if existing_id:
        log(f"channel '{NEW_CHANNEL_NAME}' already exists as ch{existing_id}; verifying")
        verify_channel(existing_id)
        if not args.dry_run:
            channel_test(token, existing_id)
            agentrouter_key = read_agentrouter_key()
            upstream_probe(agentrouter_key)
        return

    agentrouter_key = read_agentrouter_key()
    log(f"existing {MODEL} abilities: {abilities_rows(MODEL)}")
    log(f"agentrouter base_url={AGENTROUTER_BASE_URL} key_len={len(agentrouter_key)}")

    if args.dry_run:
        log("dry-run: would create channel with payload above")
        return

    backup_path = backup_db()
    log(f"backup: {backup_path}")

    payload = new_channel_payload(AGENTROUTER_BASE_URL, agentrouter_key)
    new_id = create_channel(token, payload)
    log(f"created channel ch{new_id}")

    try:
        verify_channel(new_id)
        channel_test(token, new_id)
        upstream_probe(agentrouter_key)
    except Exception as exc:
        log(f"verification failed: {exc}; rolling back channel ch{new_id}")
        delete_channel_full(token, new_id)
        raise

    log(f"DONE: {MODEL} now has pool {abilities_rows(MODEL)}; backup={backup_path}")


if __name__ == "__main__":
    main()