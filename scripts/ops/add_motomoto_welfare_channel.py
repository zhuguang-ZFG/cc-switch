#!/usr/bin/env python3
"""Onboard MotoMoto 公益 API (motomoto.lol) as a PARKED leg.

Facts (probed 2026-10-16 ~14:45 CST, this session):
- The site is itself a NewAPI instance (v1.0.0-rc.31-motomoto.49):
  `/` and `/api/status` answer 200, but **`/v1/*` is in announced
  maintenance** — 503 {"message":"维护中，请稍后再试"} on GET /v1/models and
  POST /v1/chat/completions alike, with and without the key, direct and via
  the 7897 egress (site-wide, not request gating).
- Announcement #41 (2026-10-10, type=warning, extra=大维护): "接口现在暂停…
  这次没有确定的恢复时间。恢复后会再发公告。" — no ETA; re-check the
  announcement list before any re-enable.
- Because the relay 503s, the key cannot be authenticated yet. The public
  catalog is readable via /api/pricing: codex-auto-review, gpt-5.5,
  gpt-5.6-sol, gpt-6-astra (all openai-endpoint, group default).
- All four ids are already served locally by other channels, so this leg adds
  capacity for existing names. No ModelRatio work here: gpt-6-astra's absent
  ratio is the pre-existing pending-adjudication item (untouched).
- Announcement #40 warns the operator bans abusive usage; keep usage
  conservative when this leg opens.

Placement: p10/w1 default group, created `status=2` + `auto_ban=1`
(fail-closed). Guardian only imports `status=3 && auto_ban=1` rows, so a
manually disabled row is inert — no probe churn while the upstream is down.

Re-enable condition (all three): announcement #41 superseded by a recovery
post AND `GET /v1/models` returns 200 with this key AND the management probe
(`POST /api/channel/<id>/test?model=gpt-5.5`) answers success — then
`POST /api/channel/<id>/status {"status":1}`.

Key comes from env MOTOMOTO_KEY — never argv, never the repo.
Flow: dup check -> backup -> create disabled -> abilities readback ->
no-regression relay probe on an existing id.
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
CHANNEL_NAME = "motomoto-welfare"
BASE_URL = "https://motomoto.lol"
PRIORITY = 10
WEIGHT = 1
CATALOG = ["codex-auto-review", "gpt-5.5", "gpt-5.6-sol", "gpt-6-astra"]
# No-regression probe must be an id with live legs *today*. Motomoto's own
# catalog is unusable for this: codex-auto-review and gpt-5.5 currently have
# zero enabled legs locally (all their channels parked for unrelated reasons),
# so a 503 on them proves nothing about this add. deepseek-v4.1-flash is the
# workhorse with three enabled legs (173/208/218).
REGRESSION_PROBE = "deepseek-v4.1-flash"
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


def read_keys() -> list[str]:
    raw = (os.environ.get("MOTOMOTO_KEY") or "").strip()
    if not raw.startswith("sk-") or len(raw) < 30:
        raise RuntimeError("set MOTOMOTO_KEY in env (sk-…, len>=30)")
    return [raw]


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
    (db_path.parent / "backups").mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as src, \
         closing(sqlite3.connect(dest, timeout=30)) as dst:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("backup integrity failed")
    return dest


def omp_key() -> str:
    for line in (Path.home() / ".omp" / "agent" / "models.yml").read_text(
            encoding="utf-8").splitlines():
        m = re.match(r"^\s*apiKey:\s*(sk-\S+)\s*$", line)
        if m:
            return m.group(1)
    raise RuntimeError("no OMP apiKey in models.yml")


def main() -> int:
    smoke = load_smoke()
    keys = read_keys()
    db_path = Path(smoke.DEPLOY_DIR) / "new-api.db"
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    named = [c for c in list_channels(smoke, headers) if c.get("name") == CHANNEL_NAME]
    if len(named) > 1:
        raise RuntimeError(f"duplicate {CHANNEL_NAME}: {[c['id'] for c in named]}")

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes)")

    ch_id = -1
    if named:
        ch_id = int(named[0]["id"])
        print(f"plan: {CHANNEL_NAME} exists ch{ch_id} status={named[0].get('status')} — verify-only")
    else:
        payload = {
            "name": CHANNEL_NAME, "type": 1,
            "key": keys[0], "base_url": BASE_URL,
            "models": ",".join(CATALOG),
            "group": "default", "test_model": REGRESSION_PROBE,
            "priority": PRIORITY, "weight": WEIGHT,
            "status": 2,  # fail-closed: upstream /v1/* in announced maintenance
            "auto_ban": 1,
        }
        print(f"plan: create {CHANNEL_NAME} {len(CATALOG)} ids p{PRIORITY}/w{WEIGHT} "
              f"key={mask(keys[0])} status=2 (upstream maintenance, no ETA)")
        status, resp = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/", method="POST",
            body={"mode": "single", "channel": payload}, headers=headers)
        if status != 200 or not (isinstance(resp, dict) and resp.get("success")):
            raise RuntimeError(f"create failed: HTTP {status} {str(resp)[:160]}")
        ch_id = next(int(c["id"]) for c in list_channels(smoke, headers)
                     if c.get("name") == CHANNEL_NAME)
        print(f"created ch{ch_id}")

    smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/fix", method="POST",
                    body={}, headers=headers)
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as cn:
        row = cn.execute(
            "SELECT name,status,auto_ban,priority,weight,test_model,\"group\","
            "base_url,models,cast(channel_info as text) FROM channels WHERE id=?",
            (ch_id,)).fetchone()
        ab = cn.execute("SELECT COUNT(*), COALESCE(SUM(enabled),0) FROM abilities WHERE channel_id=?",
                        (ch_id,)).fetchone()
    print(f"readback ch{ch_id}: {row[0]} st={row[1]} auto_ban={row[2]} p{row[3]}/w{row[4]} "
          f"test={row[5]} group={row[6]} base={row[7]}")
    print(f"models({len(row[8].split(','))}): {row[8]}")
    print(f"abilities: {ab[0]} rows, enabled={ab[1]} (expect 0 while st=2)")
    if row[1] != 2:
        raise RuntimeError(f"channel not parked: status={row[1]}")

    # No-regression proof: an existing id must still answer through the gateway.
    body = json.dumps({"model": REGRESSION_PROBE, "max_tokens": 8,
                       "messages": [{"role": "user", "content": "Reply with one word: OK"}]}).encode()
    req = urllib.request.Request(f"{smoke.NEWAPI_BASE}/v1/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {omp_key()}",
                                          "User-Agent": BROWSER_UA,
                                          "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            payload = json.loads(r.read().decode("utf-8", "replace"))
        content = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        print(f"relay probe {REGRESSION_PROBE}: HTTP 200 {round(time.time()-t0,1)}s "
              f"content={str(content)[:24]!r} (no regression)")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"relay probe {REGRESSION_PROBE} failed: HTTP {e.code} "
                           f"{e.read().decode('utf-8','replace')[:120]}") from e

    print(f"OK: ch{ch_id} {CHANNEL_NAME} onboarded PARKED (upstream maintenance) "
          f"backup={backup.name}")
    print("re-enable gate: announcement #41 recovery post AND GET /v1/models 200 AND "
          f"management probe green -> POST /api/channel/{ch_id}/status {{'status':1}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
