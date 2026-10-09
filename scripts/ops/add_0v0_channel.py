#!/usr/bin/env python3
"""Onboard 0v0.club as a GLM carrier in local NewAPI (2026-10-09).

User-provided key via env ZEROV0_KEY (never printed, never persisted).

Live probe 2026-10-09 (direct, chat/completions):
  GET /v1/models -> 200, 4 ids: glm-4.5-air, glm-4.6v, glm-5.3, glm-5.3-flash
  glm-4.5-air: 200 OK content="OK" (reasoning model, max_tokens=800 needed)
  glm-4.6v:    200 OK content="OK" (reasoning model)
  glm-5.3:     upstream "余额不足或无可用资源包" (key has no balance on this model)
  glm-5.3-flash: 404 model_not_found (listed but not routable)

Only glm-4.5-air and glm-4.6v are added; the other two are skipped.
No existing NewAPI channel carries these two models -> no priority conflict.

POSTURE: p30/w1 (standard backup tier, same level as the former bai-glm-5.3-flash).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import time
import urllib.request
from contextlib import closing
from pathlib import Path

DB_PATH = Path.home() / ".new-api-local" / "new-api.db"
NEWAPI_BASE = "http://127.0.0.1:3002"
CHANNEL_NAME = "0v0"
BASE_URL = "https://0v0.club"
MODELS = ["glm-4.5-air", "glm-4.6v"]
TEST_MODEL = "glm-4.6v"
PRIORITY = 30
WEIGHT = 1
AUTO_BAN = 1


def http_json(url, method="GET", body=None, headers=None, timeout=30):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    resp = urllib.request.urlopen(req, timeout=timeout)
    return resp.status, json.loads(resp.read())


def admin_auth():
    raw = (Path.home() / ".new-api-local" / "admin-credentials.json").read_text(encoding="utf-8-sig")
    creds = json.loads(raw)
    data = json.dumps({"username": creds["username"], "password": creds["password"]}).encode()
    req = urllib.request.Request(
        f"{NEWAPI_BASE}/api/user/login", data=data,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=10)
    body = json.loads(resp.read())
    token = body["data"]["access_token"]
    uid = body["data"]["user"]["id"]
    return token, uid


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"new-api-before-0v0-{stamp}.db"
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as src:
        with closing(sqlite3.connect(dest.as_posix())) as dst:
            src.backup(dst)
            integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"backup integrity_check={integrity}")
    return dest


def wait_abilities(channel_id: int, model: str, seconds: int = 90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as c:
            row = c.execute(
                'SELECT "group", enabled, priority, weight FROM abilities WHERE channel_id = ? AND model = ?',
                (channel_id, model),
            ).fetchone()
        if row:
            return row
        time.sleep(3)
    return None


def main() -> int:
    key = os.environ.get("ZEROV0_KEY", "").strip()
    if not key:
        print("ERROR: ZEROV0_KEY env var required")
        return 1

    print("plan:")
    print(f"  channel: {CHANNEL_NAME}")
    print(f"  base_url: {BASE_URL}")
    print(f"  models: {MODELS}")
    print(f"  priority={PRIORITY} weight={WEIGHT} auto_ban={AUTO_BAN}")
    print(f"  skipped: glm-5.3 (key balance exhausted), glm-5.3-flash (404 upstream)")

    token, uid = admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(uid)}
    print(f"auth ok: uid={uid}")

    # Duplicate check
    status, body = http_json(
        f"{NEWAPI_BASE}/api/channel/?p=0&page_size=500", headers=headers
    )
    items = body.get("data", {}).get("items", [])
    for c in items:
        if str(c.get("name")) == CHANNEL_NAME:
            print(f"channel {CHANNEL_NAME} already exists as ch{c['id']} — skipping create")
            return 0
        existing_models = set(str(c.get("models", "")).split(","))
        if existing_models & set(MODELS) and str(c.get("name")) != CHANNEL_NAME:
            print(f"WARNING: models {existing_models & set(MODELS)} already on ch{c['id']} {c['name']}")

    # Backup
    backup = online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes)")

    # Create channel (disabled first)
    payload = {
        "name": CHANNEL_NAME,
        "type": 1,
        "base_url": BASE_URL,
        "key": key,
        "models": ",".join(MODELS),
        "model_mapping": "{}",
        "group": "default",
        "priority": PRIORITY,
        "weight": WEIGHT,
        "auto_ban": AUTO_BAN,
        "test_model": TEST_MODEL,
    }
    status, body = http_json(
        f"{NEWAPI_BASE}/api/channel/",
        method="POST",
        body={"mode": "single", "channel": payload},
        headers=headers,
    )
    if status != 200 or not body.get("success"):
        print(f"ERROR: channel POST failed: HTTP {status} message={body.get('message')!r}")
        return 1
    new_id = None
    for c in items:
        pass
    # Re-list to find the new channel
    status, body = http_json(f"{NEWAPI_BASE}/api/channel/?p=0&page_size=500", headers=headers)
    for c in body.get("data", {}).get("items", []):
        if str(c.get("name")) == CHANNEL_NAME:
            new_id = int(c["id"])
            break
    if new_id is None:
        print("ERROR: channel not visible after POST")
        return 1
    print(f"created ch{new_id} {CHANNEL_NAME} (disabled)")

    # Enable channel
    status, body = http_json(
        f"{NEWAPI_BASE}/api/channel/{new_id}/status",
        method="POST",
        body={"status": 1},
        headers=headers,
    )
    print(f"enable: HTTP {status} success={body.get('success')}")

    # Fix abilities
    status, body = http_json(f"{NEWAPI_BASE}/api/channel/fix", method="POST", body={}, headers=headers)
    print(f"fix: HTTP {status} success={body.get('success')}")

    # Verify abilities
    for m in MODELS:
        row = wait_abilities(new_id, m)
        if not row:
            print(f"ERROR: abilities ch{new_id}/{m} absent after 90s")
            return 1
        print(f"abilities ok: ch{new_id}/{m} = group={row[0]} enabled={row[1]} p={row[2]} w={row[3]}")

    # Admin channel test
    for attempt in range(1, 4):
        status, body = http_json(
            f"{NEWAPI_BASE}/api/channel/test/{new_id}?model={TEST_MODEL}",
            headers=headers,
            timeout=120,
        )
        ok = body.get("success")
        t_ms = body.get("time")
        print(f"channel test [{attempt}/3]: HTTP {status} success={ok} time={t_ms}ms model={TEST_MODEL}")
        if ok:
            break
        time.sleep(15)

    if not body.get("success"):
        print(f"WARNING: channel test failed: {body.get('message')!r}")
        print("channel created but test failed — check manually")
    else:
        print(f"DONE: ch{new_id} {CHANNEL_NAME} enabled at p{PRIORITY}/w{WEIGHT}")
        print(f"  models: {','.join(MODELS)}")
        print(f"  base_url: {BASE_URL}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
