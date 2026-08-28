#!/usr/bin/env python3
"""Add the SenseNova token-plan kimi-k3 channel (2026-08-28).

Background
----------
The user reported that SenseTime's token plan (sensenova.cn/token-plan)
now serves Kimi K3 (included in the token plan — freeloading). ch15
``sensenova-token`` (``https://token.sensenova.cn``, p50/w10) is the plan
API channel. Its ``/v1/models`` listing (6 ids) does NOT include the
model — the listing lags — but a direct completion probe of candidate ids
found ``kimi-k3`` live: HTTP 200, ``model=kimi-k3``, content 'pong',
finish stop, 7.0s (k3 / moonshotai-k3 / kimi_k3 / k3-thinking all 404).

Design
------
Same dedicated single-model channel pattern as ch88/ch112/ch113/ch114 —
NOT an addition to ch15's models list (ch15 is a 4-model channel whose
channel-level p50/w10 is tuned for the deepseek-v4-flash compaction pool;
per-model weight is not settable, proven 2026-08-28, and a future ch15
re-tune for deepseek would silently move the k3 pool too):
- channel ``sensenova-k3`` (ch115): base_url
  ``https://token.sensenova.cn`` (no trailing /v1 — three-pit), key
  copied from ch15, models ``k3``, mapping ``k3 -> kimi-k3``, group
  ``default``, priority=50, weight=10, auto_ban=1, test_model ``k3``.
- Joins the EXISTING OMP ``k3`` pool (ch33 p50/w10 official, ch108 p49,
  ch110 p6) as an equal co-primary: 1:1 with ch33 at p50 — half of k3
  traffic moves to the token plan, stability kept by the official
  co-primary; auto_ban is per-model, so a free-tier 429 drops only k3 on
  ch115 and traffic reverts to ch33 (today's state).
- KNOWN CAVEAT: the token plan's workspace quota is SHARED across all
  models on token.sensenova.cn (glm-5.2 429 "workspace quota exceeded"
  on ch15, 2026-08-28 re-probe). k3 is the plan/designer primary (heavy
  contexts); a quota squeeze can 429 the deepseek-v4-flash compaction
  head (ch15 p50) on the same plan. The compaction ladder absorbs this
  (cooldown -> qwen tail). If sustained 429s appear, lower ch115 weight.
- OMP side: zero changes (model id ``k3`` already registered).
- Local billing: k3's ModelRatio (2) applies to ch115 traffic too — the
  free upstream still bills the local wallet at the k3 rate (cosmetic,
  same accepted state as other pooled ids).

Safety: online DB snapshot first, API+DB readback verify (new row + ch33
row unchanged), 12-request gateway functional test with channel
attribution from the ``logs`` table, automatic channel DELETE +
abilities-row cleanup on failure. Dry-run by default.
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

CH_KEY_SOURCE = 15    # sensenova-token (key donor; NOT modified)
MODEL = "k3"
UPSTREAM_ID = "kimi-k3"
BASE_URL = "https://token.sensenova.cn"
NEW_CHANNEL_NAME = "sensenova-k3"
NEW_CHANNEL_PRIORITY = 50
NEW_CHANNEL_WEIGHT = 10
SYNC_POLL_SECONDS = 180
SYNC_POLL_INTERVAL = 5
FUNCTIONAL_REQUESTS = 12

DB_PATH = Path.home() / ".new-api-local" / "new-api.db"
GATEWAY_BASE = "http://127.0.0.1:3002"


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    return parser.parse_args()


def fetch_channels(smoke, headers: dict[str, str]) -> list[dict]:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=200", headers=headers
    )
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"channel list failed: HTTP {status}")
    items = body.get("data") or []
    if isinstance(items, dict):
        items = items.get("items") or []
    return [i for i in items if isinstance(i, dict)]


def post_channel(smoke, headers: dict[str, str], payload: dict) -> None:
    """坑1: POST create requires the {"mode":"single","channel":{...}} wrapper."""
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/",
        method="POST",
        body={"mode": "single", "channel": payload},
        headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"channel POST failed: HTTP {status} message={message!r}")


def delete_channel(smoke, headers: dict[str, str], channel_id: int) -> None:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}",
        method="DELETE",
        headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"channel DELETE failed: HTTP {status} message={message!r}")


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = backup_dir / f"new-api-before-sensenova-k3-{stamp}.db"
    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        dst.close()
        src.close()
    if integrity != "ok":
        raise RuntimeError(f"backup integrity check failed: {integrity}")
    return destination


def db_row(channel_id: int, model: str):
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as connection:
        return connection.execute(
            'SELECT "group", enabled, priority, weight FROM abilities WHERE channel_id=? AND model=?',
            (channel_id, model),
        ).fetchone()


def delete_abilities_row(channel_id: int, model: str) -> None:
    """SQL write path: must COMMIT (legacy sqlite3 autocommit off)."""
    connection = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=rwc", uri=True, timeout=30)
    try:
        connection.execute(
            "DELETE FROM abilities WHERE channel_id=? AND model=?", (channel_id, model)
        )
        connection.commit()
    finally:
        connection.close()


def wait_for_abilities_row(channel_id: int) -> None:
    deadline = time.time() + SYNC_POLL_SECONDS
    while time.time() < deadline:
        row = db_row(channel_id, MODEL)
        if row is not None:
            return
        time.sleep(SYNC_POLL_INTERVAL)
    row = db_row(channel_id, MODEL)
    if row is None:
        print("abilities: sync goroutine did not create the row; inserted directly")
        connection = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=rwc", uri=True, timeout=30)
        try:
            connection.execute(
                'INSERT INTO abilities (channel_id, model, "group", enabled, priority, weight) '
                "VALUES (?, ?, 'default', 1, ?, ?)",
                (channel_id, MODEL, NEW_CHANNEL_PRIORITY, NEW_CHANNEL_WEIGHT),
            )
            connection.commit()
        finally:
            connection.close()


def db_key(channel_id: int) -> str:
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as connection:
        row = connection.execute("SELECT key FROM channels WHERE id=?", (channel_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"ch{channel_id} key not found")
    return row[0]


def read_gateway_key() -> str:
    """Read the OMP zg-newapi apiKey from the live models.yml (never printed)."""
    import re

    text = (Path.home() / ".omp" / "agent" / "models.yml").read_text(encoding="utf-8")
    match = re.search(
        r"^  zg-newapi:\n(?:    .*\n)*?    apiKey:\s*(\S+)", text, flags=re.M
    )
    if not match:
        raise RuntimeError("zg-newapi apiKey not found in live models.yml")
    return match.group(1)


def functional_test(api_key: str) -> dict[int, int]:
    """Send small completions through the local gateway and attribute channels
    via the logs table (type=2 = normal request log)."""
    import urllib.error
    import urllib.request

    mark = time.time()
    ok = 0
    errors: list[str] = []
    for _ in range(FUNCTIONAL_REQUESTS):
        body = json.dumps(
            {
                "model": MODEL,
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "max_tokens": 24,
            }
        ).encode()
        req = urllib.request.Request(
            f"{GATEWAY_BASE}/v1/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
            ok += 1
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:160]
            errors.append(f"HTTP {exc.code}: {detail}")
        except Exception as exc:  # noqa: BLE001 - report and continue
            errors.append(f"{type(exc).__name__}: {exc}")
    if errors:
        print(f"functional: {len(errors)}/{FUNCTIONAL_REQUESTS} request errors, e.g. {errors[0]}")
    if ok == 0:
        raise RuntimeError(f"functional test: all {FUNCTIONAL_REQUESTS} gateway requests failed")
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as connection:
        rows = connection.execute(
            "SELECT channel_id FROM logs "
            "WHERE model_name = ? AND created_at > ? AND type = 2",
            (MODEL, mark),
        ).fetchall()
    distribution: dict[int, int] = {}
    for (channel_id,) in rows:
        distribution[channel_id] = distribution.get(channel_id, 0) + 1
    return distribution


def verify(channels: list[dict], new_id: int) -> None:
    by_id = {int(c["id"]): c for c in channels}
    new = by_id.get(new_id)
    if new is None:
        raise RuntimeError(f"new channel {NEW_CHANNEL_NAME} (id={new_id}) missing from readback")
    if str(new.get("models")) != MODEL:
        raise RuntimeError(f"new channel models wrong: {new.get('models')!r}")
    mapping_raw = str(new.get("model_mapping") or "")
    mapping = json.loads(mapping_raw) if mapping_raw.strip() else {}
    if mapping.get(MODEL) != UPSTREAM_ID:
        raise RuntimeError(f"new channel mapping wrong: {mapping}")
    if str(new.get("base_url")).rstrip("/") != BASE_URL:
        raise RuntimeError(f"new channel base_url wrong: {new.get('base_url')!r}")
    if str(new.get("group")) != "default":
        raise RuntimeError(f"new channel group wrong: {new.get('group')!r}")
    row_new = db_row(new_id, MODEL)
    if row_new != ("default", 1, NEW_CHANNEL_PRIORITY, NEW_CHANNEL_WEIGHT):
        raise RuntimeError(f"new channel abilities row wrong: {row_new}")
    row_primary = db_row(33, MODEL)
    if row_primary != ("default", 1, 50, 10):
        raise RuntimeError(f"ch33 k3 abilities row changed unexpectedly: {row_primary}")


def main() -> int:
    args = parse_args()
    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    channels = fetch_channels(smoke, headers)
    if any(str(c.get("name")) == NEW_CHANNEL_NAME for c in channels):
        print(f"channel {NEW_CHANNEL_NAME!r} already exists; nothing to do")
        return 0
    print(f"plan: POST {NEW_CHANNEL_NAME!r} base_url={BASE_URL} key=ch{CH_KEY_SOURCE} "
          f"models={MODEL} mapping {MODEL}->{UPSTREAM_ID} prio={NEW_CHANNEL_PRIORITY} "
          f"weight={NEW_CHANNEL_WEIGHT}")

    if not args.apply:
        print("dry-run: no changes made")
        return 0

    key = db_key(CH_KEY_SOURCE)

    backup = online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    payload = {
        "name": NEW_CHANNEL_NAME,
        "type": 1,
        "base_url": BASE_URL,
        "key": key,
        "models": MODEL,
        "model_mapping": json.dumps({MODEL: UPSTREAM_ID}, separators=(",", ":")),
        "group": "default",
        "priority": NEW_CHANNEL_PRIORITY,
        "weight": NEW_CHANNEL_WEIGHT,
        "auto_ban": 1,
        "test_model": MODEL,
    }
    new_id: int | None = None
    try:
        post_channel(smoke, headers, payload)
        after = fetch_channels(smoke, headers)
        match = [c for c in after if str(c.get("name")) == NEW_CHANNEL_NAME]
        if not match:
            raise RuntimeError("new channel not visible after POST")
        new_id = int(match[0]["id"])
        print(f"channel created: ch{new_id} {NEW_CHANNEL_NAME}")

        wait_for_abilities_row(new_id)
        row_new = db_row(new_id, MODEL)
        print(f"abilities: ch{new_id} row {row_new}")

        readback = fetch_channels(smoke, headers)
        verify(readback, new_id)
        print("verify ok: API readback + abilities row")

        distribution = functional_test(read_gateway_key())
        print(f"functional ok: {FUNCTIONAL_REQUESTS} requests, "
              f"channel distribution: {distribution}")
        if new_id not in distribution:
            print(f"WARNING: ch{new_id} did not receive any of the "
                  f"{FUNCTIONAL_REQUESTS} requests; observe logs before assuming a fault")
        print(f"OK: {MODEL} pooled ch33+ch{new_id} 1:1 at p50 (ch108/ch110 unchanged); "
              f"backup={backup.name}")
        return 0
    except Exception:
        if new_id is not None:
            try:
                delete_channel(smoke, headers, new_id)
                print(f"rollback: ch{new_id} deleted")
            except Exception as error:
                print(f"rollback warning: ch{new_id} delete failed: {error}")
            try:
                delete_abilities_row(new_id, MODEL)
                print(f"rollback: ch{new_id} abilities row removed")
            except Exception as error:
                print(f"rollback warning: ch{new_id} abilities cleanup failed: {error}")
        print(f"rollback attempted; full snapshot={backup.name}")
        raise


if __name__ == "__main__":
    sys.exit(main())
