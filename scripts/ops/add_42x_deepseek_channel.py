#!/usr/bin/env python3
"""42x.shop DeepSeek 渠道接入（2026-10-06，fail-closed 禁用态）。

背景：用户提供 `https://api.42x.shop` + key（`sk-` 格式）。上游为 NewAPI 系站点。
2026-10-06 实测：`/v1/models` 仅 3 模型 — `deepseek-flash-free`（本 token 403 无权限）、
`deepseek-v4-flash-0731` 与 `deepseek-v4-flash-0731-free`（均 502 上游故障，多轮复现）。
故本脚本以 **status=2 禁用态** 落库（注册 abilities、fail-closed、不接客），
上游恢复后启用只需一条 status POST（见文末）。

约定：key 经环境变量 `FORTYTWOX_KEY` 传入，不落盘不回显；dry-run 默认。

回滚：删除渠道（或保持禁用）；DB 快照 `backups/new-api-before-42x-deepseek-*.db`。

Enable later:  curl -X POST http://127.0.0.1:3002/api/channel/{id}/status -d '{"status":1}'  (带 admin 头)
Run: FORTYTWOX_KEY=... python3 scripts/ops/add_42x_deepseek_channel.py [--apply]
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
DEPLOY_DIR = Path(r"C:\Users\zhugu\.new-api-local")
GATEWAY_BASE = "http://127.0.0.1:3002"

CHANNEL_NAME = "42x-deepseek-v4-flash-0731"
BASE_URL = "https://api.42x.shop"
MODELS = "deepseek-v4-flash-0731,deepseek-v4-flash-0731-free"
TEST_MODEL = "deepseek-v4-flash-0731"
PRIORITY = 20
WEIGHT = 1
TAG = "42x-deepseek"


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load smoke helper: {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"new-api-before-42x-deepseek-{time.strftime('%Y%m%d-%H%M%S')}.db"
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


def db_all(sql, params=()):
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as c:
        return c.execute(sql, params).fetchall()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    args = parser.parse_args()

    key = os.environ.get("FORTYTWOX_KEY", "").strip()
    if not key:
        raise RuntimeError("FORTYTWOX_KEY env var required (never persisted/logged)")

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    existing = [c for c in fetch_channels(smoke, headers) if str(c.get("name")) == CHANNEL_NAME]
    if existing:
        raise RuntimeError(f"channel {CHANNEL_NAME} already exists (id={existing[0].get('id')})")

    print("plan:")
    print(f"  create ch {CHANNEL_NAME} type=1 base_url={BASE_URL} status=2 (fail-closed)")
    print(f"  models={MODELS} test_model={TEST_MODEL} p={PRIORITY} w={WEIGHT} auto_ban=1 tag={TAG}")
    print("  verify: DB readback (status=2), abilities enabled=0, gateway fail-closed 503")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    payload = {
        "name": CHANNEL_NAME,
        "type": 1,
        "base_url": BASE_URL,
        "key": key,
        "models": MODELS,
        "group": "default",
        "priority": PRIORITY,
        "weight": WEIGHT,
        "auto_ban": 1,
        "status": 2,
        "test_model": TEST_MODEL,
        "tag": TAG,
    }
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/",
        method="POST",
        body={"mode": "single", "channel": payload},
        headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        msg = body.get("message") if isinstance(body, dict) else str(body)[:160]
        raise RuntimeError(f"channel POST failed: HTTP {status} message={msg!r}")
    print("POST ok")

    after = [c for c in fetch_channels(smoke, headers) if str(c.get("name")) == CHANNEL_NAME]
    if not after:
        raise RuntimeError(f"{CHANNEL_NAME} not visible after POST")
    channel_id = int(after[0]["id"])
    print(f"created ch{channel_id} {CHANNEL_NAME}")

    row = db_all("SELECT name, status, priority, weight, models, auto_ban FROM channels WHERE id = ?", (channel_id,))
    print("DB readback:", row)
    if not row or row[0][1] != 2 or row[0][5] != 1:
        raise RuntimeError(f"readback mismatch: {row}")

    deadline = time.monotonic() + 60
    ab = []
    while time.monotonic() < deadline:
        ab = db_all("SELECT model, enabled FROM abilities WHERE channel_id = ? ORDER BY model", (channel_id,))
        if len(ab) >= 2:
            break
        time.sleep(4)
    print("abilities:", ab)
    if sorted(m for m, _ in ab) != ["deepseek-v4-flash-0731", "deepseek-v4-flash-0731-free"] or any(e != 0 for _, e in ab):
        raise RuntimeError(f"abilities mismatch (expect both disabled): {ab}")

    # gateway fail-closed check with the local probe token
    probe = json.loads((DEPLOY_DIR / "client-token.json").read_text(encoding="utf-8"))
    probe_key = probe.get("api_key") or probe.get("key")
    gs, gb = smoke.http_json(
        f"{GATEWAY_BASE}/v1/chat/completions",
        method="POST",
        body={"model": "deepseek-v4-flash-0731-free", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8},
        headers={"Authorization": f"Bearer {probe_key}"},
        timeout=60,
    )
    print(f"gateway probe (disabled channel): HTTP {gs} {str(gb)[:200]}")
    if gs == 200:
        raise RuntimeError("gateway routed to a disabled channel?! investigate before proceeding")

    print(f"DONE: ch{channel_id} {CHANNEL_NAME} registered disabled (upstream 502 since 10-06; enable after site recovers)")
    print(f"enable later: POST /api/channel/{channel_id}/status {{\"status\":1}} (admin headers)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
