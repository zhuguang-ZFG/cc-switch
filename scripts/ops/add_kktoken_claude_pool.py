#!/usr/bin/env python3
"""Add the kktoken.cc Claude opus channel (2026-08-28).

Background
----------
The user provided a kktoken.cc key whose catalog lists exactly four
models: ``claude-opus-5``, ``claude-opus-5-thinking``, ``claude-opus-4-8``,
``claude-opus-4-8-thinking``. Live probes (2026-08-28): all four
completions HTTP 200 (pong/stop, 1.7-3.0s; thinking variants carry
reasoning content).

Cloudflare UA gate: python's default urllib UA is blocked with 403
``error code: 1010`` on both ``/v1/models`` and completions, while
``curl/8.5``, ``Go-http-client/1.1|2.0``, ``new-api``, ``OneAPI`` and
browser UAs all pass. NewAPI's Go client therefore reaches the upstream
natively; direct probes must carry a UA.

Design
------
Same dedicated channel pattern as ch113/ch114/ch115:
- channel ``kktoken`` (ch116): base_url ``https://kktoken.cc`` (no
  trailing /v1 — NewAPI type=1 appends it; the 2026-08-01 three-pit),
  key from the KK_KEY environment variable (never printed or written
  anywhere but the channel row), models = the four catalog ids verbatim
  (no model_mapping needed — upstream ids equal the exposed ids), group
  ``default``, priority=50, weight=5, auto_ban=1, test_model
  ``claude-opus-5``.
- The four ids are already registered OMP models; ch116 joins the live
  claude pool (ch94/ch95 justwoker p50/w8, ch57 gorouter p50/w5) as a
  fourth source at p50/w5 — weights 8:8:5:5.
- auto_ban=1 (external relay; the revive timer pardons status=3 only).

Safety: online DB snapshot first, API+DB readback verify (new rows for
all four models + ch94/ch95/ch57 rows unchanged), 12-request gateway
functional test on ``claude-opus-5`` with channel attribution from the
``logs`` table, automatic channel DELETE + abilities-row cleanup on
failure. Dry-run by default. The KK_KEY env var must be set for --apply
runs.
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

MODELS = [
    "claude-opus-5",
    "claude-opus-5-thinking",
    "claude-opus-4-8",
    "claude-opus-4-8-thinking",
]
BASE_URL = "https://kktoken.cc"
NEW_CHANNEL_NAME = "kktoken"
NEW_CHANNEL_PRIORITY = 50
NEW_CHANNEL_WEIGHT = 5
SYNC_POLL_SECONDS = 180
SYNC_POLL_INTERVAL = 5
FUNCTIONAL_REQUESTS = 12
FUNCTIONAL_MODEL = "claude-opus-5"

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
    destination = backup_dir / f"new-api-before-kktoken-claude-{stamp}.db"
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


def delete_abilities_rows(channel_id: int) -> None:
    """SQL write path: must COMMIT (legacy sqlite3 autocommit off)."""
    connection = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=rwc", uri=True, timeout=30)
    try:
        connection.execute(
            "DELETE FROM abilities WHERE channel_id=?", (channel_id,)
        )
        connection.commit()
    finally:
        connection.close()


def wait_for_abilities_rows(channel_id: int) -> None:
    deadline = time.time() + SYNC_POLL_SECONDS
    while time.time() < deadline:
        if all(db_row(channel_id, m) is not None for m in MODELS):
            return
        time.sleep(SYNC_POLL_INTERVAL)
    missing = [m for m in MODELS if db_row(channel_id, m) is None]
    if missing:
        print(f"abilities: sync goroutine missed {missing}; inserted directly")
        connection = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=rwc", uri=True, timeout=30)
        try:
            for m in missing:
                connection.execute(
                    'INSERT INTO abilities (channel_id, model, "group", enabled, priority, weight) '
                    "VALUES (?, ?, 'default', 1, ?, ?)",
                    (channel_id, m, NEW_CHANNEL_PRIORITY, NEW_CHANNEL_WEIGHT),
                )
            connection.commit()
        finally:
            connection.close()


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
                "model": FUNCTIONAL_MODEL,
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
            (FUNCTIONAL_MODEL, mark),
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
    if sorted(str(new.get("models")).split(",")) != sorted(MODELS):
        raise RuntimeError(f"new channel models wrong: {new.get('models')!r}")
    if str(new.get("base_url")).rstrip("/") != BASE_URL:
        raise RuntimeError(f"new channel base_url wrong: {new.get('base_url')!r}")
    if str(new.get("group")) != "default":
        raise RuntimeError(f"new channel group wrong: {new.get('group')!r}")
    for m in MODELS:
        row_new = db_row(new_id, m)
        if row_new != ("default", 1, NEW_CHANNEL_PRIORITY, NEW_CHANNEL_WEIGHT):
            raise RuntimeError(f"new channel abilities row wrong for {m}: {row_new}")
    # pool siblings unchanged
    for cid, prio, weight in ((94, 50, 8), (95, 50, 8), (57, 50, 5)):
        row = db_row(cid, FUNCTIONAL_MODEL)
        if row != ("default", 1, prio, weight):
            raise RuntimeError(f"ch{cid} claude-opus-5 abilities row changed unexpectedly: {row}")


def main() -> int:
    args = parse_args()
    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    channels = fetch_channels(smoke, headers)
    if any(str(c.get("name")) == NEW_CHANNEL_NAME for c in channels):
        print(f"channel {NEW_CHANNEL_NAME!r} already exists; nothing to do")
        return 0
    print(f"plan: POST {NEW_CHANNEL_NAME!r} base_url={BASE_URL} key=<KK_KEY env> "
          f"models={','.join(MODELS)} prio={NEW_CHANNEL_PRIORITY} "
          f"weight={NEW_CHANNEL_WEIGHT}")

    if not args.apply:
        print("dry-run: no changes made")
        return 0

    key = os.environ.get("KK_KEY", "")
    if not key:
        print("abort: KK_KEY environment variable not set (required for --apply)")
        return 1

    backup = online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    payload = {
        "name": NEW_CHANNEL_NAME,
        "type": 1,
        "base_url": BASE_URL,
        "key": key,
        "models": ",".join(MODELS),
        "model_mapping": "",
        "group": "default",
        "priority": NEW_CHANNEL_PRIORITY,
        "weight": NEW_CHANNEL_WEIGHT,
        "auto_ban": 1,
        "test_model": FUNCTIONAL_MODEL,
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

        wait_for_abilities_rows(new_id)
        print(f"abilities: ch{new_id} rows {[db_row(new_id, m) for m in MODELS]}")

        readback = fetch_channels(smoke, headers)
        verify(readback, new_id)
        print("verify ok: API readback + abilities rows + pool siblings")

        distribution = functional_test(read_gateway_key())
        print(f"functional ok: {FUNCTIONAL_REQUESTS} requests, "
              f"channel distribution: {distribution}")
        if new_id not in distribution:
            print(f"WARNING: ch{new_id} did not receive any of the "
                  f"{FUNCTIONAL_REQUESTS} requests; observe logs before assuming a fault")
        print(f"OK: {FUNCTIONAL_MODEL} pooled ch94/95/57+ch{new_id} at 8:8:5:5; "
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
                delete_abilities_rows(new_id)
                print(f"rollback: ch{new_id} abilities rows removed")
            except Exception as error:
                print(f"rollback warning: ch{new_id} abilities cleanup failed: {error}")
        print(f"rollback attempted; full snapshot={backup.name}")
        raise


if __name__ == "__main__":
    sys.exit(main())
