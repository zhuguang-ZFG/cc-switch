#!/usr/bin/env python3
"""OpenCode Go 上游改名修复：ch130 `space-bunny-free` -> `space-bunny`（2026-10-06）。

背景：上游 /zen/go/v1/models 于本日下午撤下 `space-bunny-free`、改挂 `space-bunny`
（直连旧 id 400 "Model is unavailable"，新 id chat 200）。NewAPI ch130 仍挂旧 id，
连带 OMP advisor 全量 400。本脚本只改 ch130 的 name/models/test_model/tag（key、
header_override、posture 全不动），重建 abilities，走网关实弹验证渠道归因。

回滚：
  1) 全库快照 `~/.new-api-local/backups/new-api-before-opencode-go-spacebunny-*.db`（integrity=ok）；
  2) 字段级回滚件 `~/.new-api-local/backups/opencode-go-spacebunny-rename-pre-*.json`
     （name/models/test_model/tag 的改前值），按同法 PUT 回去即可；
  3) OMP 侧：models.yml / config.yml 的 `.bak-20261006-spacebunny` 还原。

Run: python3 scripts/ops/fix_opencode_go_space_bunny_20261006.py [--apply]  (dry-run 默认)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
DB_PATH = Path.home() / ".new-api-local" / "new-api.db"
GATEWAY_BASE = "http://127.0.0.1:3002"

CHANNEL_ID = 130
OLD_NAME = "opencode-go-space-bunny-free"
OLD_MODEL = "space-bunny-free"
NEW_NAME = "opencode-go-space-bunny"
NEW_MODEL = "space-bunny"
NEW_TAG = "limited-time-free"
EXPECTED_WEIGHT = 5
EXPECTED_PRIORITY = 0


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
    destination = backup_dir / (
        f"new-api-before-opencode-go-spacebunny-{time.strftime('%Y%m%d-%H%M%S')}.db"
    )
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


def db_one(sql, params=()):
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as c:
        return c.execute(sql, params).fetchone()


def db_all(sql, params=()):
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as c:
        return c.execute(sql, params).fetchall()


def read_gateway_key() -> str:
    """OMP zg-newapi apiKey from live models.yml (never printed)."""
    import re

    text = (Path.home() / ".omp" / "agent" / "models.yml").read_text(encoding="utf-8")
    match = re.search(r"^  zg-newapi:\n(?:    .*\n)*?    apiKey:\s*(\S+)", text, flags=re.M)
    if not match:
        raise RuntimeError("zg-newapi apiKey not found in live models.yml")
    return match.group(1)


def put_channel(smoke, headers, item: dict, new_key: str) -> None:
    """Fork PUT contract: list-endpoint object minus ``status``, explicit key."""
    body = {k: v for k, v in item.items() if k != "status"}
    body["key"] = new_key
    status, resp = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/", method="PUT", body=body, headers=headers
    )
    if status != 200 or not isinstance(resp, dict) or not resp.get("success"):
        message = resp.get("message") if isinstance(resp, dict) else None
        raise RuntimeError(f"PUT ch{item.get('id')} failed: HTTP {status} message={message!r}")


def channel_fix(smoke, headers) -> None:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/fix", method="POST", body={}, headers=headers
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"channel fix failed: HTTP {status} message={message!r}")


def wait_ability(channel_id: int, model: str, seconds: int = 120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        row = db_one(
            'SELECT "group", enabled, priority, weight FROM abilities '
            "WHERE channel_id = ? AND model = ?",
            (channel_id, model),
        )
        if row is not None:
            return row
        time.sleep(4)
    return None


def wait_ability_gone(channel_id: int, model: str, seconds: int = 120) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        row = db_one(
            "SELECT model FROM abilities WHERE channel_id = ? AND model = ?",
            (channel_id, model),
        )
        if row is None:
            return True
        time.sleep(4)
    return False


def functional_test(smoke, gateway_key: str, model: str) -> tuple[bool, str, int | None]:
    mark = time.time()
    status, body = smoke.http_json(
        f"{GATEWAY_BASE}/v1/chat/completions",
        method="POST",
        body={
            "model": model,
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": 64,
        },
        headers={"Authorization": f"Bearer {gateway_key}"},
        timeout=120,
    )
    if status != 200:
        return False, f"HTTP {status}: {json.dumps(body)[:160]}", None
    rows = db_all(
        "SELECT channel_id FROM logs WHERE model_name = ? AND created_at > ? AND type = 2 "
        "ORDER BY id DESC LIMIT 1",
        (model, mark),
    )
    channel_id = int(rows[0][0]) if rows else None
    return True, "200", channel_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    args = parser.parse_args()

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    channels = fetch_channels(smoke, headers)
    by_id = {int(c["id"]): c for c in channels}
    target = by_id.get(CHANNEL_ID)
    if target is None:
        raise RuntimeError(f"ch{CHANNEL_ID} missing from gateway list")

    current = {
        "name": target.get("name"),
        "models": target.get("models"),
        "test_model": target.get("test_model"),
        "tag": target.get("tag"),
    }
    header_override = str(target.get("header_override") or "")
    if "x-opencode-session" not in header_override:
        raise RuntimeError("ch130 header_override missing session header; refusing to touch")
    if current["name"] == NEW_NAME and current["models"] == NEW_MODEL:
        print("already renamed; nothing to do")
        return 0
    if current["name"] != OLD_NAME or current["models"] != OLD_MODEL:
        raise RuntimeError(f"unexpected ch130 pre-state: {current!r}")

    print("plan:")
    print(f"  PUT ch{CHANNEL_ID}: name {OLD_NAME} -> {NEW_NAME}")
    print(f"  PUT ch{CHANNEL_ID}: models/test_model {OLD_MODEL} -> {NEW_MODEL}, tag -> {NEW_TAG}")
    print("  key/header_override/priority/weight untouched; POST /api/channel/fix; abilities re-check")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")
    rollback = backup.parent / (
        f"opencode-go-spacebunny-rename-pre-{time.strftime('%Y%m%d-%H%M%S')}.json"
    )
    rollback.write_text(
        json.dumps(
            {
                "channel_id": CHANNEL_ID,
                "before": current,
                "after": {
                    "name": NEW_NAME,
                    "models": NEW_MODEL,
                    "test_model": NEW_MODEL,
                    "tag": NEW_TAG,
                },
                "note": "PUT these fields back via the same fork PUT contract to roll back names/models.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"rollback fields: {rollback.name}")

    key_row = db_one("SELECT key FROM channels WHERE id = ?", (CHANNEL_ID,))
    if not key_row:
        raise RuntimeError("ch130 key missing in DB")
    channel_key = key_row[0]

    updated = dict(target)
    updated["name"] = NEW_NAME
    updated["models"] = NEW_MODEL
    updated["test_model"] = NEW_MODEL
    updated["tag"] = NEW_TAG
    put_channel(smoke, headers, updated, channel_key)

    readback = db_one(
        "SELECT name, models, test_model, tag, key FROM channels WHERE id = ?", (CHANNEL_ID,)
    )
    if not readback:
        raise RuntimeError("ch130 vanished after PUT")
    if (
        readback[0] != NEW_NAME
        or readback[1] != NEW_MODEL
        or readback[2] != NEW_MODEL
        or readback[4] != channel_key
    ):
        raise RuntimeError(f"readback mismatch after PUT: {readback[:4]!r}")
    print("PUT ok; DB readback matches (key unchanged)")

    channel_fix(smoke, headers)
    print("channel fix ok")

    problems: list[str] = []
    row_ab = wait_ability(CHANNEL_ID, NEW_MODEL)
    if row_ab != ("default", 1, EXPECTED_PRIORITY, EXPECTED_WEIGHT):
        problems.append(f"abilities ch130/{NEW_MODEL} = {row_ab!r}")
    if not wait_ability_gone(CHANNEL_ID, OLD_MODEL):
        problems.append(f"stale abilities row ch130/{OLD_MODEL} still present")
    if problems:
        raise RuntimeError("verify failed: " + "; ".join(problems))
    print(f"abilities ok: ch130/{NEW_MODEL} = (default,1,{EXPECTED_PRIORITY},{EXPECTED_WEIGHT}); old row gone")

    gateway_key = read_gateway_key()
    ok, detail, attr = functional_test(smoke, gateway_key, NEW_MODEL)
    flag = "OK" if ok and attr == CHANNEL_ID else "FAIL"
    print(f"functional {flag}: {NEW_MODEL} -> {detail}, attributed ch{attr} (expected ch{CHANNEL_ID})")
    if not ok or attr != CHANNEL_ID:
        raise RuntimeError(f"functional test failed for {NEW_MODEL}: {detail} attr={attr}")

    print("DONE: ch130 serves space-bunny; update OMP models.yml/config.yml + runbook next")
    return 0


if __name__ == "__main__":
    sys.exit(main())
