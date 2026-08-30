#!/usr/bin/env python3
"""Aggregate a second source for `glm-5.3` via the local agentrouter proxy.

Uses the same local agentrouter key as ch45 (agentrouter) but creates a dedicated
single-model channel so the pool is independent of ch45's disabled Claude/GPT
models. GLM is not subject to the agentrouter.org Claude/GPT batch quota.

Runbook:
- source key donor : ch45 (agentrouter local key)
- new channel      : ch120 (or next available id) named `agentrouter-glm-5.3`
- primary source   : ch108 (whyyin, p49/w5) -- unchanged
- fallback source  : agentrouter-glm-5.3 (p40/w5)
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

HOME = Path.home()
DB_PATH = HOME / ".new-api-local" / "new-api.db"
BACKUP_DIR = HOME / ".new-api-local" / "backups"
SECRETS_PATH = HOME / ".omp" / "guardian" / "secrets.json"
MODELS_YAML_PATH = HOME / ".omp" / "agent" / "models.yml"
BASE = "http://127.0.0.1:3002"

MODEL = "glm-5.3"
CH_KEY_SOURCE = 45          # agentrouter local proxy
NEW_CHANNEL_NAME = "agentrouter-glm-5.3"
PRIORITY = 40
WEIGHT = 5
BACKUP_PREFIX = "new-api-before-agentrouter-glm53"


def _redact(text: str) -> str:
    if not text:
        return text
    # redact any 51-char sk-... keys
    return re.sub(r"(sk-[A-Za-z0-9]{48})[A-Za-z0-9]{3}", r"\1***", text)


def log(msg: str) -> None:
    print(_redact(msg), flush=True)


def fatal(msg: str) -> None:
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
    # NewAPI POST /api/channel/ may not return data.id in this fork; fallback to name lookup.
    new_id = (body.get("data") or {}).get("id")
    if new_id:
        return int(new_id)
    # fallback: query by name
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


def verify_channel(token: str, channel_id: int) -> None:
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


def read_gateway_key() -> str:
    """Read OMP zg-newapi relay apiKey from live models.yml (never printed)."""
    text = MODELS_YAML_PATH.read_text(encoding="utf-8")
    match = re.search(r"zg-newapi:\s*[\s\S]*?apiKey:\s*(sk-[A-Za-z0-9]+)", text)
    if not match:
        fatal("cannot find zg-newapi apiKey in models.yml")
    return match.group(1)


def channel_test(token: str, channel_id: int) -> bool:
    body = api_call(token, "GET", f"/api/channel/test/{channel_id}", timeout=60)
    ok = body.get("success") is True
    log(f"channel test ch{channel_id}: success={ok} message={body.get('message','')[:120]}")
    return ok


def functional_test(api_key: str, channel_ids: set[int]) -> bool:
    """Send a few relay requests; ensure all succeed and channel_ids appear in logs."""
    body_json = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": "Reply with exactly: pong"}],
        "max_tokens": 100,
        "stream": False,
    }).encode()
    seen: set[int] = set()
    for i in range(1, 4):
        req = urllib.request.Request(f"{BASE}/v1/chat/completions", data=body_json, method="POST")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"].get("content", "")
                finish = data["choices"][0]["finish_reason"]
                log(f"functional {i}/3: finish={finish} content={content[:40]!r}")
        except Exception as exc:
            log(f"functional {i}/3 FAILED: {exc}")
            return False
        time.sleep(0.3)

    # read last 3 logs for this model to see which channels were used
    conn = db_conn(True)
    rows = conn.execute(
        "SELECT channel_id, other FROM logs WHERE model_name=? ORDER BY created_at DESC LIMIT 3",
        (MODEL,),
    ).fetchall()
    conn.close()
    for r in rows:
        seen.add(r["channel_id"])
        other = (r["other"] or "")[:120]
        log(f"log ch{r['channel_id']} other={other}")
    missing = channel_ids - seen
    if missing:
        log(f"WARNING: these channel ids not seen in last 3 logs: {missing}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="probe only, do not create channel")
    args = parser.parse_args()

    token = load_admin_token()
    existing_id = find_channel_by_name(NEW_CHANNEL_NAME)
    if existing_id:
        log(f"channel '{NEW_CHANNEL_NAME}' already exists as ch{existing_id}; verifying")
        verify_channel(token, existing_id)
        if not args.dry_run:
            channel_test(token, existing_id)
            relay_key = read_gateway_key()
            functional_test(relay_key, {existing_id})
        return

    # read key donor
    donor = get_channel_row(CH_KEY_SOURCE)
    base_url = donor["base_url"]
    donor_key = donor["key"]
    if not base_url or not donor_key:
        fatal(f"ch{CH_KEY_SOURCE} missing base_url or key")

    log(f"key donor ch{CH_KEY_SOURCE}: base_url={base_url} key_len={len(donor_key)}")
    log(f"existing {MODEL} abilities: {abilities_rows(MODEL)}")

    if args.dry_run:
        log("dry-run: would create channel with payload above")
        return

    backup_path = backup_db()
    log(f"backup: {backup_path}")

    payload = new_channel_payload(base_url, donor_key)
    new_id = create_channel(token, payload)
    log(f"created channel ch{new_id}")

    try:
        verify_channel(token, new_id)
        channel_test(token, new_id)
        relay_key = read_gateway_key()
        functional_test(relay_key, {new_id})
    except Exception as exc:
        log(f"verification failed: {exc}; rolling back channel ch{new_id}")
        delete_channel_full(token, new_id)
        raise

    log(f"DONE: {MODEL} now has pool {abilities_rows(MODEL)}; backup={backup_path}")


if __name__ == "__main__":
    main()
