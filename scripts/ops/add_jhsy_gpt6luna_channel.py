#!/usr/bin/env python3
"""Onboard the jhsy welfare relay (https://ai.jhsy0721.xyz) gpt-6-luna leg.

Facts (2026-10-12, this session):
- Key (env-provided, never printed) exposes exactly one model: gpt-6-luna
  (GET /v1/models -> 1 model). Direct chat 3/3 200, 2.3-3.4s.
- Pool today: ch200 dddai-gpt-6-luna p20/w5 head, ch188-191 jojatoken p-20,
  ch206 yanglechu disabled, ch145/174 unrelated. ModelRatio gpt-6-luna = 0.
- ch201 jhsy-glm (same host, old key) sits status=2 — untouched here; the
  new key does not list glm-5.3-flash.

Placement: priority 30 / weight 5 — welfare quota consumed first (user's
"薅羊毛" intent), above paid dddai p20. auto_ban=1 so a dead free leg exits
automatically and dddai/jojatoken catch. group=default mirrors ch200 (all
tokens are default/any/agent; no Free-group rows exist on luna legs).

Flow: dup check -> online backup -> POST create disabled -> management
test -> enable -> abilities readback -> gateway relay probe x2 via OMP
token + log attribution. Rollback = disable on post-create failure.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
CHANNEL_NAME = "jhsy-gpt-6-luna"
BASE_URL = "https://ai.jhsy0721.xyz"  # type=1 appends /v1
MODEL = "gpt-6-luna"
PRIORITY = 30
WEIGHT = 5
OMP_MODELS_YML = Path.home() / ".omp" / "agent" / "models.yml"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["newapi_local_smoke"] = module
    spec.loader.exec_module(module)
    return module


def mask(key: str) -> str:
    return f"{key[:4]}...{key[-4:]} (len={len(key)})"


def read_key() -> str:
    raw = os.environ.get("JHSY_KEY_B64") or os.environ.get("JHSY_KEY")
    if not raw:
        raise RuntimeError("set JHSY_KEY_B64 (base64) or JHSY_KEY in env")
    import base64

    raw = raw.strip()
    key = raw
    if not raw.startswith("sk-"):
        try:
            key = base64.b64decode(raw).decode()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"JHSY_KEY_B64 not decodable: {exc}") from exc
    key = key.strip()
    if not key.startswith("sk-") or len(key) < 20:
        raise RuntimeError("decoded key does not look like sk-…")
    return key


def list_channels(smoke, headers) -> list[dict]:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=200", headers=headers
    )
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"channel list failed: HTTP {status}")
    data = body.get("data") or {}
    items = data.get("items") if isinstance(data, dict) else None
    if items is None:
        raise RuntimeError(f"channel list shape unexpected: {str(body)[:160]}")
    return items


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"newapi-before-{CHANNEL_NAME}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as src, \
         closing(sqlite3.connect(dest, timeout=30)) as dst:
        src.backup(dst)
        ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        raise RuntimeError(f"backup integrity: {ok}")
    return dest


def read_omp_relay_token() -> str:
    text = OMP_MODELS_YML.read_text(encoding="utf-8")
    match = re.search(r"^\s*apiKey:\s*(sk-\S+)\s*$", text, re.MULTILINE)
    if match is None:
        raise RuntimeError(f"no sk- apiKey found in {OMP_MODELS_YML}")
    return match.group(1)


def relay_probe(smoke, model: str, attempts: int = 3) -> str:
    key = read_omp_relay_token()
    body = {
        "model": model,
        "max_tokens": 800,  # relay 防思考模型吃满 max_tokens 导致空 content
        "messages": [{"role": "user", "content": "Reply with exactly one word: OK"}],
    }
    backoff = (2, 8, 20)
    last = ""
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            time.sleep(backoff[min(attempt - 2, len(backoff) - 1)])
        req = urllib.request.Request(
            f"{smoke.NEWAPI_BASE}/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}", "User-Agent": BROWSER_UA,
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode())
                content = (data["choices"][0]["message"].get("content") or "").strip()
                if content:
                    print(f"relay probe {model} HTTP 200 content={content[:24]!r} "
                          f"({attempt}/{attempts})")
                    return content
                last = "empty content"
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code} {exc.read().decode()[:120]}"
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        print(f"relay probe {model} attempt {attempt}/{attempts} failed: {last}")
    raise RuntimeError(f"relay probe failed for {model}: {last}")


def main() -> int:
    smoke = load_smoke()
    key = read_key()
    db_path = Path(smoke.DEPLOY_DIR) / "new-api.db"
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    items = list_channels(smoke, headers)
    named = [c for c in items if c.get("name") == CHANNEL_NAME]
    if len(named) > 1:
        raise RuntimeError(f"duplicate channel name {CHANNEL_NAME}: {[c['id'] for c in named]}")
    existing = named[0] if named else None

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes)")

    channel_id: int | None = None
    created = False
    try:
        if existing is not None:
            channel_id = int(existing["id"])
            print(f"plan: {CHANNEL_NAME} exists as ch{channel_id} "
                  f"status={existing.get('status')} — verify-only")
        else:
            payload = {
                "name": CHANNEL_NAME,
                "type": 1,
                "key": key,
                "base_url": BASE_URL,
                "models": MODEL,
                "group": "default",
                "test_model": MODEL,
                "priority": PRIORITY,
                "weight": WEIGHT,
                "status": 2,
                "auto_ban": 1,
            }
            print(f"plan: create {CHANNEL_NAME} p{PRIORITY}/w{WEIGHT} auto_ban=1 "
                  f"key={mask(key)}")
            status, resp = smoke.http_json(
                f"{smoke.NEWAPI_BASE}/api/channel/", method="POST",
                body={"mode": "single", "channel": payload}, headers=headers)
            if status != 200 or not isinstance(resp, dict) or not resp.get("success"):
                msg = resp.get("message") if isinstance(resp, dict) else resp
                raise RuntimeError(f"create failed: HTTP {status} {msg}")
            created = True
            items = list_channels(smoke, headers)
            new = next(i for i in items if i.get("name") == CHANNEL_NAME)
            channel_id = int(new["id"])

        # management probe (works while disabled)
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={MODEL}",
            headers=headers, timeout=90)
        ok = status == 200 and isinstance(body, dict) and body.get("success")
        print(f"management probe ch{channel_id}: HTTP {status} "
              f"success={bool(ok)} {str(body)[:160]}")
        if not ok:
            raise RuntimeError("management probe not green — not enabling")

        # enable + readback
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}/status",
            method="POST", body={"status": 1}, headers=headers)
        if status != 200 or not (isinstance(body, dict) and body.get("success")):
            raise RuntimeError(f"enable failed: HTTP {status} {str(body)[:120]}")
        with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as cn:
            time.sleep(3)
            rows = cn.execute(
                'SELECT "group",enabled,priority,weight FROM abilities '
                "WHERE channel_id=? AND model=?", (channel_id, MODEL)).fetchall()
        print(f"abilities ch{channel_id}/{MODEL}: {rows}")

        relay_probe(smoke, MODEL)

        with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as cn:
            row = cn.execute(
                "SELECT channel_id,model_name FROM logs ORDER BY id DESC LIMIT 1").fetchone()
        print(f"last log: ch={row[0]} model={row[1]} "
              f"({'attribution OK' if row[0] == channel_id else 'NOT jhsy leg'})")
        print(f"OK: ch{channel_id} {CHANNEL_NAME} live at p{PRIORITY}/w{WEIGHT} "
              f"backup={backup.name}")
        return 0
    except Exception as error:  # noqa: BLE001
        print(f"FAIL: {error}")
        if channel_id is not None and created:
            try:
                smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}/status",
                                method="POST", body={"status": 2}, headers=headers)
                print(f"rollback: ch{channel_id} disabled (full snapshot kept)")
            except Exception as rb_error:  # noqa: BLE001
                print(f"rollback FAILED: {rb_error}; backup={backup.name}")
        elif channel_id is not None:
            print(f"channel {channel_id} left untouched; backup={backup.name}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
