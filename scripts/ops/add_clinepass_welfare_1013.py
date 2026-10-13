#!/usr/bin/env python3
"""Onboard the 2026-10-13 shared ClinePass welfare keys (api.cline.bot).

Facts (probed 2026-10-13, this session):
- OpenAI-compat endpoint confirmed: POST https://api.cline.bot/api/v1/
  chat/completions with Bearer sk_… authenticates (user prefers OpenAI shape).
- BOTH keys are alive but INSIDE the 5h rolling cap: 429 INFERENCE_CAP_ERROR
  (probed ~17:55 CST: key1 "resets in 4h 1m", key2 "3h 59m"). Public welfare
  keys get strip-mined instantly; auth passed (not 401), so onboard
  FAIL-CLOSED and enable after both windows reopen (~21:55 CST).
- Catalog now 14 ids (recommended-models .clinePass): the 12 in
  add_clinepass_channel.py plus mimo-v2.6-flash / mimo-v2.6-pro.
- ch203 clinepass (old key, weekly cap, manual-disabled 10-16 reset) is
  UNTOUCHED. Expiry: key1 2026-10-14 17:48, key2 2026-10-17 15:03 CST.

Placement: p10/w1 auto_ban=1 — quota-uncontrollable welfare leg, never pool
head. 429 is not in AutomaticDisableStatusCodes so the leg degrades
gracefully inside pools; a dead (401) key auto-bans the channel.
Multi-key: newline pair, mode=multi_to_single + channel_info DB remediation
(ch221 precedent; never PUT after the fix). mimo-v2.6-flash/pro need 0/0
pricing rows (new pool names).

Keys come from env CLINE_WELFARE_KEY1/2 — never argv, never the repo.
Flow: dup check -> backup -> create disabled -> pricing -> multi-key fix ->
abilities readback -> fail-closed 503 proof on sole-leg id.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
CHANNEL_NAME = "clinepass-welfare-1013"
BASE_URL = "https://api.cline.bot/api"  # fork appends /v1/chat/completions
PRIORITY = 10
WEIGHT = 1
SOLE_LEG_TEST = "mimo-v2.6-pro"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)
MODEL_MAP = {
    "deepseek-v4-pro": "cline-pass/deepseek-v4-pro",
    "deepseek-v4.1-flash": "cline-pass/deepseek-v4.1-flash",
    "glm-5.3": "cline-pass/glm-5.3",
    "glm-5.3-flash": "cline-pass/glm-5.3-flash",
    "kimi-k3": "cline-pass/kimi-k3",
    "mimo-v2.5": "cline-pass/mimo-v2.5",
    "mimo-v2.5-pro": "cline-pass/mimo-v2.5-pro",
    "mimo-v2.6-flash": "cline-pass/mimo-v2.6-flash",
    "mimo-v2.6-pro": "cline-pass/mimo-v2.6-pro",
    "minimax-m3": "cline-pass/minimax-m3",
    "muse-spark-1.3-contributor": "cline-pass/muse-spark-1.3-contributor",
    "qwen3.7-max": "cline-pass/qwen3.7-max",
    "qwen3.7-plus": "cline-pass/qwen3.7-plus",
    "qwen3.8-max": "cline-pass/qwen3.8-max",
}


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["newapi_local_smoke"] = module
    spec.loader.exec_module(module)
    return module


def read_keys() -> list[str]:
    keys = []
    for var in ("CLINE_WELFARE_KEY1", "CLINE_WELFARE_KEY2"):
        raw = (os.environ.get(var) or "").strip()
        if raw.startswith("sk_") and len(raw) > 30:
            keys.append(raw)
    if not keys:
        raise RuntimeError("set CLINE_WELFARE_KEY1/CLINE_WELFARE_KEY2 in env")
    return keys


def mask(key: str) -> str:
    return f"{key[:7]}...{key[-4:]} (len={len(key)})"


def list_channels(smoke, headers) -> list[dict]:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=200", headers=headers)
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"channel list failed: HTTP {status}")
    data = body.get("data") or []
    items = data.get("items") if isinstance(data, dict) else data
    return [c for c in (items or []) if isinstance(c, dict)]


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


def ensure_pricing(smoke, headers, pool) -> None:
    need = [m for m in ("mimo-v2.6-flash", "mimo-v2.6-pro") if m not in pool]
    if not need:
        print("pricing: mimo-v2.6-* already present")
        return
    for m in need:
        pool[m] = 0
    status, resp = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/option/", method="PUT",
        body={"key": "ModelRatio", "value": json.dumps(pool)}, headers=headers)
    if status != 200 or not (isinstance(resp, dict) and resp.get("success")):
        raise RuntimeError(f"ModelRatio update failed: HTTP {status} {str(resp)[:140]}")
    print(f"ModelRatio: added 0 for {need}")


def main() -> int:
    smoke = load_smoke()
    keys = read_keys()
    db_path = Path(smoke.DEPLOY_DIR) / "new-api.db"
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as cn:
        ratio_pool = json.loads(cn.execute(
            "SELECT value FROM options WHERE key='ModelRatio'").fetchone()[0])

    items = list_channels(smoke, headers)
    named = [c for c in items if c.get("name") == CHANNEL_NAME]
    if len(named) > 1:
        raise RuntimeError(f"duplicate {CHANNEL_NAME}: {[c['id'] for c in named]}")

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes)")

    ch_id = -1
    try:
        if named:
            ch_id = int(named[0]["id"])
            print(f"plan: {CHANNEL_NAME} exists ch{ch_id} status={named[0].get('status')} — verify-only")
        else:
            payload = {
                "name": CHANNEL_NAME, "type": 1,
                "key": "\n".join(keys), "base_url": BASE_URL,
                "models": ",".join(MODEL_MAP),
                "model_mapping": json.dumps(MODEL_MAP, ensure_ascii=False),
                "group": "default", "test_model": "glm-5.3-flash",
                "priority": PRIORITY, "weight": WEIGHT,
                "status": 2,  # fail-closed until 5h cap reopens
                "auto_ban": 1,
            }
            print(f"plan: create {CHANNEL_NAME} {len(MODEL_MAP)} maps "
                  f"p{PRIORITY}/w{WEIGHT} keys={[mask(k) for k in keys]} status=2")
            status, resp = smoke.http_json(
                f"{smoke.NEWAPI_BASE}/api/channel/", method="POST",
                body={"mode": "multi_to_single", "channel": payload}, headers=headers)
            if status != 200 or not (isinstance(resp, dict) and resp.get("success")):
                raise RuntimeError(f"create failed: HTTP {status} {str(resp)[:160]}")
            ch_id = next(int(c["id"]) for c in list_channels(smoke, headers)
                         if c.get("name") == CHANNEL_NAME)

        ensure_pricing(smoke, headers, ratio_pool)

        # multi-key remediation (ch221 precedent): never PUT afterwards.
        target = {"is_multi_key": True, "multi_key_size": len(keys),
                  "multi_key_status_list": {}, "multi_key_polling_index": 0,
                  "multi_key_mode": "polling"}
        with closing(sqlite3.connect(str(db_path), timeout=30)) as cn:
            row = cn.execute("SELECT CAST(channel_info AS TEXT) FROM channels WHERE id=?",
                             (ch_id,)).fetchone()
            info = {}
            if row and row[0]:
                try:
                    info = json.loads(row[0])
                except json.JSONDecodeError:
                    info = {}
            ok = (info.get("is_multi_key") and info.get("multi_key_size") == len(keys)
                  and info.get("multi_key_mode") == "polling")
            if not ok:
                cn.execute("UPDATE channels SET channel_info = CAST(? AS BLOB) WHERE id = ?",
                           (json.dumps(target), ch_id))
                cn.commit()
            row2 = cn.execute("SELECT CAST(channel_info AS TEXT), status FROM channels WHERE id=?",
                              (ch_id,)).fetchone()
        print(f"channel_info: {'ok' if ok else 'fixed'} readback={row2[0][:90]} status={row2[1]}")
        if row2[1] != 2:
            smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/{ch_id}/status",
                            method="POST", body={"status": 2}, headers=headers)
            print("enforced status=2 (fail-closed)")

        smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/fix", method="POST",
                        body={}, headers=headers)
        with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as cn:
            rows = cn.execute("SELECT COUNT(*), SUM(enabled) FROM abilities WHERE channel_id=?",
                              (ch_id,)).fetchone()
        print(f"abilities ch{ch_id}: {rows[0]} rows, enabled={rows[1] or 0} (expect 0)")

        # fail-closed proof: sole-leg id must 503 while disabled
        omp_key = ""
        import re
        for line in (Path.home() / ".omp" / "agent" / "models.yml").read_text(
                encoding="utf-8").splitlines():
            m = re.match(r"^\s*apiKey:\s*(sk-\S+)\s*$", line)
            if m:
                omp_key = m.group(1)
                break
        body = json.dumps({"model": SOLE_LEG_TEST, "max_tokens": 8,
                           "messages": [{"role": "user", "content": "hi"}]}).encode()
        req = urllib.request.Request(f"{smoke.NEWAPI_BASE}/v1/chat/completions",
                                     data=body, headers={
                                         "Authorization": f"Bearer {omp_key}",
                                         "User-Agent": BROWSER_UA,
                                         "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raise RuntimeError(f"expected 503 fail-closed, got HTTP {r.status}")
        except urllib.error.HTTPError as e:
            if e.code == 503:
                print(f"fail-closed proof: {SOLE_LEG_TEST} -> 503 (correct, channel disabled)")
            else:
                raise RuntimeError(f"expected 503, got {e.code}: {e.read().decode()[:120]}")

        print(f"OK: ch{ch_id} {CHANNEL_NAME} created DISABLED p{PRIORITY}/w{WEIGHT} "
              f"backup={backup.name}")
        print("TODO after 5h cap reopens (~21:55 CST): management probe green -> "
              f"POST /api/channel/{ch_id}/status {{'status':1}}")
        return 0
    except Exception as error:  # noqa: BLE001
        print(f"FAIL: {error}")
        if ch_id > 0:
            try:
                smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/{ch_id}/status",
                                method="POST", body={"status": 2}, headers=headers)
                print(f"rollback: ch{ch_id} disabled; backup={backup.name}")
            except Exception as rb:  # noqa: BLE001
                print(f"rollback FAILED: {rb}; backup={backup.name}")
        else:
            print(f"no channel to roll back; backup={backup.name}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
