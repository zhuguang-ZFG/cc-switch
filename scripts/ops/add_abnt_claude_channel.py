#!/usr/bin/env python3
"""api.abnt.it Claude 渠道接入（2026-10-08，fail-closed 禁用态）。

背景：用户提供 `https://api.abnt.it` + key（`sk-` 格式）。上游为 NewAPI 系站点
（400 错误体带 request id / "manual Claude thinking" 校验语，与 muyuan/42x 同款）。
2026-10-08 实测：`/v1/models` 200 共 7 模型（claude-opus-4-8/4-7/4-6/4-5-20251101/
sonnet-4-5/haiku-4-5/haiku-4-5-thinking）；但 chat 与 /v1/messages 全部确定性 502
（CF 风格纯文本体、3 轮复现、~1s 即返）；`-thinking` 变体在校验层即 400
（max_tokens 需 >1024），提高后同样 502。即站点控制面活着、推理渠道池死。
CF 仅 ban python-urllib UA（1010），带浏览器 UA 即通，无需 header_override。

故本脚本以 **status=2 禁用态** 落库（注册 abilities、fail-closed、不接客），
上游恢复后启用只需一条 status POST（见文末）。注意 claude-opus-4-8 等模型与
zg-newapi-anthropic 池重叠，启用前须按 runbook 实弹复测再入池。

约定：key 经环境变量 `ABNT_KEY` 传入，不落盘不回显；dry-run 默认。

回滚：删除渠道（或保持禁用）；DB 快照 `backups/new-api-before-abnt-claude-*.db`。

Enable later:  curl -X POST http://127.0.0.1:3002/api/channel/{id}/status -d '{"status":1}'  (带 admin 头)
Run: ABNT_KEY=... python3 scripts/ops/add_abnt_claude_channel.py [--apply]
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

CHANNEL_NAME = "abnt-claude"
BASE_URL = "https://api.abnt.it"
MODELS = ",".join([
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-opus-4-5-20251101",
    "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5-20251001",
    "claude-haiku-4-5-20251001-thinking",
])
TEST_MODEL = "claude-opus-4-8"
PRIORITY = 20
WEIGHT = 1
TAG = "abnt-claude"
EXPECTED_MODELS = sorted(set(MODELS.split(",")))


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load smoke helper: {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fetch_channels(smoke, headers):
    # fork API 每页硬顶 100 且 p=0 为特例页；真翻页从 p=1 起（2026-10-08 实证）。
    items = []
    for page in range(1, 21):
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/?p={page}&page_size=100", headers=headers
        )
        if status != 200 or not isinstance(body, dict):
            raise RuntimeError(f"channel list failed: HTTP {status}")
        data = body.get("data") or []
        chunk = data.get("items") if isinstance(data, dict) else data
        if not isinstance(chunk, list) or not chunk:
            break
        items.extend(chunk)
        total = data.get("total") if isinstance(data, dict) else None
        if not isinstance(total, int) or len(items) >= total:
            break
    return [i for i in items if isinstance(i, dict)]


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"new-api-before-abnt-claude-{time.strftime('%Y%m%d-%H%M%S')}.db"
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

    key = os.environ.get("ABNT_KEY", "").strip()
    if not key:
        raise RuntimeError("ABNT_KEY env var required (never persisted/logged)")

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    existing = [c for c in fetch_channels(smoke, headers) if str(c.get("name")) == CHANNEL_NAME]
    if existing:
        raise RuntimeError(f"channel {CHANNEL_NAME} already exists (id={existing[0].get('id')})")

    print("plan:")
    print(f"  create ch {CHANNEL_NAME} type=1 base_url={BASE_URL} status=2 (fail-closed)")
    print(f"  models=7 claude ids, test_model={TEST_MODEL} p={PRIORITY} w={WEIGHT} auto_ban=1 tag={TAG}")
    print("  verify: DB readback (status=2), abilities 7 rows enabled=0")
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
    print("DB readback:", row[0][:2], row[0][4:])
    if not row or row[0][1] != 2 or row[0][5] != 1:
        raise RuntimeError(f"readback mismatch: {row}")

    deadline = time.monotonic() + 90
    ab = []
    while time.monotonic() < deadline:
        ab = db_all("SELECT model, enabled FROM abilities WHERE channel_id = ? ORDER BY model", (channel_id,))
        if len(ab) >= 7:
            break
        time.sleep(4)
    print("abilities:", [m for m, _ in ab], "enabled:", sorted({e for _, e in ab}))
    if sorted(m for m, _ in ab) != EXPECTED_MODELS or any(e != 0 for _, e in ab):
        raise RuntimeError(f"abilities mismatch (expect 7 disabled): {ab}")

    print(f"DONE: ch{channel_id} {CHANNEL_NAME} registered disabled (upstream chat 502 on 10-08; enable only after re-probe 200)")
    print(f"enable later: POST /api/channel/{channel_id}/status {{\"status\":1}} (admin headers)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
