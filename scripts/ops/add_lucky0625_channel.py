#!/usr/bin/env python3
"""Onboard the lucky0625 welfare relay (https://new.lucky0625.qzz.io).

Facts (2026-10-12/13, this session):
- Key (env-provided, never printed): 30 ids listed; chat probe: trio
  chat-claude-fable-5.1 (15.8s) / chat-claude-opus-5 (12.8s) / chat-gpt-6astra
  (16.7s) + agnes-2.5-flash + gemini 2.5-flash/-lite/-pro, 3-flash,
  3-flash-preview, 3-pro-preview all 200 (paced). Hard limit 5 req/min/key
  (429 counts failures) -> low-priority backstop leg, never pool head.
- agnes-image-*/video-* reject /v1/chat (media endpoints) -> excluded.
- ModelRatio missing for all chat-* and most gemini ids -> must add 0/0
  (free welfare key, precedent: opencode-go kept models -> 0) or NewAPI
  throws "price not configured" on first use.
- chat-claude-opus-5 / chat-gpt-6astra / gemini-3.8-flash have zero live
  legs -> relay adds real coverage; attribution provable on those.

Flow: dup check -> online backup -> create disabled -> management probe ->
enable -> pricing merge via /api/option/ -> abilities readback -> gateway
relay probes on zero-leg ids -> rollback = disable on failure.
"""
from __future__ import annotations

import argparse
import base64
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
CHANNEL_NAME = "lucky0625"
BASE_URL = "https://new.lucky0625.qzz.io"
TEST_MODEL = "gemini-2.5-flash-lite"
PRIORITY = -20
WEIGHT = 1
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
    raw = (os.environ.get("LUCKY_KEY_B64") or os.environ.get("LUCKY_KEY") or "").strip()
    if not raw:
        raise RuntimeError("set LUCKY_KEY_B64 or LUCKY_KEY in env")
    key = raw if raw.startswith("sk-") else base64.b64decode(raw).decode().strip()
    if not key.startswith("sk-") or len(key) < 20:
        raise RuntimeError("key does not look like sk-…")
    return key


def list_channels(smoke, headers) -> list[dict]:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=200", headers=headers)
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"channel list failed: HTTP {status}")
    return (body.get("data") or {}).get("items") or []


def online_backup(db_path: Path) -> Path:
    dest = (db_path.parent / "backups"
            / f"newapi-before-{CHANNEL_NAME}-{time.strftime('%Y%m%d-%H%M%S')}.db")
    db_path.parent.joinpath("backups").mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as src, \
         closing(sqlite3.connect(dest, timeout=30)) as dst:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("backup integrity failed")
    return dest


def read_omp_relay_token() -> str:
    text = OMP_MODELS_YML.read_text(encoding="utf-8")
    match = re.search(r"^\s*apiKey:\s*(sk-\S+)\s*$", text, re.MULTILINE)
    assert match is not None, "no sk- apiKey in models.yml"
    return match.group(1)


def relay_probe(smoke, model: str, attempts: int = 3) -> str:
    key = read_omp_relay_token()
    body = {"model": model, "max_tokens": 48,
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}]}
    last = ""
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            time.sleep(15)
        req = urllib.request.Request(
            f"{smoke.NEWAPI_BASE}/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}", "User-Agent": BROWSER_UA,
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode())
                content = (data["choices"][0]["message"].get("content") or "").strip()
                if content:
                    print(f"relay {model}: 200 {content[:24]!r} ({attempt}/{attempts})")
                    return content
                last = "empty content"
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code} {exc.read().decode()[:120]}"
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        print(f"relay {model} attempt {attempt}/{attempts}: {last}")
    raise RuntimeError(f"relay probe failed for {model}: {last}")


def ensure_pricing(smoke, headers, models: list[str]) -> None:
    with closing(sqlite3.connect(f"file:{Path(smoke.DEPLOY_DIR)/'new-api.db'}"
                                 f"?mode=ro", uri=True)) as cn:
        vals = {r[0]: r[1] for r in cn.execute(
            "SELECT key,value FROM options WHERE key IN ('ModelRatio','CompletionRatio')")}
    for opt_key in ("ModelRatio", "CompletionRatio"):
        ratios = json.loads(vals.get(opt_key, "{}"))
        missing = [m for m in models if m not in ratios]
        if not missing:
            print(f"{opt_key}: all present")
            continue
        for m in missing:
            ratios[m] = 0
        status, resp = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/option/", method="PUT",
            body={"key": opt_key, "value": json.dumps(ratios)}, headers=headers)
        if status != 200 or not (isinstance(resp, dict) and resp.get("success")):
            raise RuntimeError(f"{opt_key} update failed: HTTP {status} {str(resp)[:120]}")
        print(f"{opt_key}: added 0 for {missing}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", required=True, help="comma-separated verified model ids")
    args = parser.parse_args()
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    assert TEST_MODEL in models, "TEST_MODEL must be in --models"

    smoke = load_smoke()
    key = read_key()
    db_path = Path(smoke.DEPLOY_DIR) / "new-api.db"
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    items = list_channels(smoke, headers)
    named = [c for c in items if c.get("name") == CHANNEL_NAME]
    if len(named) > 1:
        raise RuntimeError(f"duplicate {CHANNEL_NAME}: {[c['id'] for c in named]}")
    existing = named[0] if named else None

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes)")

    channel_id: int | None = None
    created = False
    try:
        if existing is not None:
            channel_id = int(existing["id"])
            print(f"plan: {CHANNEL_NAME} exists ch{channel_id} — verify-only")
        else:
            payload = {"name": CHANNEL_NAME, "type": 1, "key": key, "base_url": BASE_URL,
                       "models": ",".join(models), "model_mapping": "",
                       "group": "default", "test_model": TEST_MODEL,
                       "priority": PRIORITY, "weight": WEIGHT, "status": 2, "auto_ban": 1}
            print(f"plan: create {CHANNEL_NAME} {len(models)} models "
                  f"p{PRIORITY}/w{WEIGHT} key={mask(key)}")
            status, resp = smoke.http_json(
                f"{smoke.NEWAPI_BASE}/api/channel/", method="POST",
                body={"mode": "single", "channel": payload}, headers=headers)
            if status != 200 or not (isinstance(resp, dict) and resp.get("success")):
                raise RuntimeError(f"create failed: HTTP {status} {str(resp)[:160]}")
            created = True
            new = next(i for i in list_channels(smoke, headers)
                       if i.get("name") == CHANNEL_NAME)
            channel_id = int(new["id"])

        ensure_pricing(smoke, headers, models)

        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={TEST_MODEL}",
            headers=headers, timeout=90)
        if not (status == 200 and isinstance(body, dict) and body.get("success")):
            raise RuntimeError(f"management probe not green: HTTP {status} {str(body)[:160]}")
        print(f"management probe ch{channel_id}: 200 {str(body)[:80]}")

        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}/status",
            method="POST", body={"status": 1}, headers=headers)
        if status != 200 or not (isinstance(body, dict) and body.get("success")):
            raise RuntimeError(f"enable failed: HTTP {status}")
        time.sleep(3)
        with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as cn:
            n = cn.execute("SELECT COUNT(*) FROM abilities WHERE channel_id=? AND enabled=1",
                           (channel_id,)).fetchone()[0]
        print(f"abilities ch{channel_id}: {n}/{len(models)} enabled")

        # attribution on zero-leg ids (forced to this channel)
        for probe_model in ("chat-claude-opus-5", "gemini-3.8-flash"):
            relay_probe(smoke, probe_model)
            with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro",
                                         uri=True)) as cn:
                row = cn.execute("SELECT channel_id FROM logs WHERE model_name=? "
                                 "ORDER BY id DESC LIMIT 1", (probe_model,)).fetchone()
            print(f"attribution {probe_model}: ch={row and row[0]}"
                  f" {'OK' if row and row[0] == channel_id else 'NOT LUCKY'}")

        print(f"OK: ch{channel_id} {CHANNEL_NAME} live p{PRIORITY}/w{WEIGHT} "
              f"backup={backup.name}")
        return 0
    except Exception as error:  # noqa: BLE001
        print(f"FAIL: {error}")
        if channel_id is not None and created:
            try:
                smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}/status",
                                method="POST", body={"status": 2}, headers=headers)
                print(f"rollback: ch{channel_id} disabled; backup={backup.name}")
            except Exception as rb:  # noqa: BLE001
                print(f"rollback FAILED: {rb}; backup={backup.name}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
