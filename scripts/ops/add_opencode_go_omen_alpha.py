#!/usr/bin/env python3
"""Add omen-alpha to opencode.ai/zen/go as a dedicated single-model channel
(2026-09-04).

Background
----------
User reported "opencode zen added omen-alpha". Supplier scan (Go key +
browser UA, keys never printed/persisted):

- https://opencode.ai/zen/v1 (ch96 opencode-zen-free endpoint): /v1/models
  (66 models) does NOT list omen-alpha; direct chat returns HTTP 401
  ModelError "Model omen-alpha is not supported" -> zen free tier has no
  such model; ch96 must NOT carry it (docs lag / stealth launch check).
- https://opencode.ai/zen/go (ch101 opencode-go-mimo endpoint): /v1/models
  lists omen-alpha; direct chat returns HTTP 200 finish=stop -> available.

omen-alpha is a Go-plan model. Go is a flat $60/mo subscription; marginal
cost is zero (same rationale as mimo-v2.5 = 0 on ch101), so ModelRatio=0.

Mechanism finding (same as qwen3.8-max-free pooling, see
``add_qwen38_max_free_pool.py``): the ``abilities`` table is a derived
artifact of the channel row; per-model weight can only be controlled via
channel-level fields. ch101 hosts mimo-v2.5, so extending it would entangle
weights. Therefore the model gets a **dedicated single-model channel** (same
pattern as ch112/ch113/ch117): own key entry, own weight vector, zero
entanglement.

Change
------
1. POST new channel ``opencode-go-omen-alpha`` (坑1: ``{"mode":"single",
   "channel":{...}}`` wrapper, no ``status`` field): base_url
   ``https://opencode.ai/zen/go`` (no ``/v1`` - NewAPI type=1 appends it),
   key copied from ch101, type=1, models ``omen-alpha`` (no mapping: exposed
   id == upstream id), group ``default``, priority=0, weight=5, auto_ban=1,
   test_model ``omen-alpha``, browser-UA header_override (same as ch101;
   Cloudflare 1010 otherwise).
2. ModelRatio ``omen-alpha -> 0``: Go flat subscription, marginal cost zero.
3. No ch101 change (key donor only). No OMP change (unknown role/context
   window; register on demand).

Safety: online DB snapshot first, API+DB readback verify, 12-request gateway
functional test with channel attribution from the ``logs`` table, automatic
channel DELETE + abilities-row cleanup on failure. Dry-run by default.
Idempotent: re-run probes and verifies only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")

MODEL = "omen-alpha"
CH_KEY_SOURCE = 101    # opencode-go-mimo (key donor; NOT modified)
NEW_CHANNEL_NAME = "opencode-go-omen-alpha"
NEW_CHANNEL_PRIORITY = 0
NEW_CHANNEL_WEIGHT = 5
SYNC_POLL_SECONDS = 180
SYNC_POLL_INTERVAL = 5
FUNCTIONAL_REQUESTS = 12
FUNCTIONAL_MAX_TOKENS = 120
MODEL_RATIO_OPTION = "ModelRatio"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

DB_PATH = Path.home() / ".new-api-local" / "new-api.db"
GATEWAY_BASE = "http://127.0.0.1:3002"


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load smoke helper: {SMOKE_PATH}")
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
    destination = backup_dir / (
        f"new-api-before-omen-alpha-channel-{time.strftime('%Y%m%d-%H%M%S')}.db"
    )
    if destination.exists():
        raise RuntimeError(f"backup already exists: {destination}")
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


def db_row(channel_id: int, model: str):
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as connection:
        return connection.execute(
            "SELECT \"group\", enabled, priority, weight FROM abilities "
            "WHERE channel_id = ? AND model = ?",
            (channel_id, model),
        ).fetchone()


def db_key(channel_id: int) -> str:
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as connection:
        row = connection.execute(
            "SELECT key FROM channels WHERE id = ?", (channel_id,)
        ).fetchone()
    if row is None or not row[0]:
        raise RuntimeError(f"ch{channel_id} has no key")
    return row[0]


def delete_abilities_row(channel_id: int, model: str) -> None:
    """SQL write path: must COMMIT (legacy sqlite3 autocommit off; closing()
    without commit rolls the DML back - caught 2026-08-28 on the ch110 plan)."""
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=rw", uri=True, timeout=30)) as connection:
        connection.execute(
            "DELETE FROM abilities WHERE channel_id = ? AND model = ?",
            (channel_id, model),
        )
        connection.commit()


def wait_for_abilities_row(channel_id: int) -> None:
    """Wait for the sync goroutine to materialize the new channel's row;
    insert directly (with commit) if it never appears."""
    deadline = time.monotonic() + SYNC_POLL_SECONDS
    while time.monotonic() < deadline:
        if db_row(channel_id, MODEL) is not None:
            return
        time.sleep(SYNC_POLL_INTERVAL)
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=rw", uri=True, timeout=30)) as connection:
        connection.execute(
            "INSERT INTO abilities (channel_id, model, \"group\", enabled, priority, weight) "
            "VALUES (?, ?, 'default', 1, ?, ?)",
            (channel_id, MODEL, NEW_CHANNEL_PRIORITY, NEW_CHANNEL_WEIGHT),
        )
        connection.commit()
    print("abilities: sync goroutine did not create the row; inserted directly")


def read_gateway_key() -> str:
    """Read the OMP zg-newapi apiKey from the live models.yml (never printed)."""
    text = (Path.home() / ".omp" / "agent" / "models.yml").read_text(encoding="utf-8")
    match = re.search(
        r"^  zg-newapi:\n(?:    .*\n)*?    apiKey:\s*(\S+)", text, flags=re.M
    )
    if not match:
        raise RuntimeError("zg-newapi apiKey not found in live models.yml")
    return match.group(1)


def get_option_db(db_path: Path, key: str) -> str:
    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        row = connection.execute(
            "SELECT value FROM options WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        raise RuntimeError(f"option {key!r} not found")
    return row[0]


def put_option(smoke, headers: dict[str, str], key: str, value: str) -> None:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/option/",
        method="PUT",
        body={"key": key, "value": value},
        headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"option {key!r} update failed: HTTP {status} message={message!r}")


def merge_ratio(current: str, model: str, ratio: int) -> str:
    try:
        ratios = json.loads(current)
        if not isinstance(ratios, dict):
            raise ValueError("not a dict")
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"ModelRatio option is not a JSON dict: {exc}") from exc
    if ratios.get(model) == ratio:
        return json.dumps(ratios, separators=(",", ":"), sort_keys=True)
    ratios[model] = ratio
    return json.dumps(ratios, separators=(",", ":"), sort_keys=True)


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
                "max_tokens": FUNCTIONAL_MAX_TOKENS,
            }
        ).encode()
        req = urllib.request.Request(
            f"{GATEWAY_BASE}/v1/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
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
    if CH_KEY_SOURCE not in by_id:
        raise RuntimeError(f"ch{CH_KEY_SOURCE} (key donor) missing from readback")
    new = by_id.get(new_id)
    if new is None:
        raise RuntimeError(f"new channel {NEW_CHANNEL_NAME} (id={new_id}) missing from readback")
    if str(new.get("models")) != MODEL:
        raise RuntimeError(f"new channel models wrong: {new.get('models')!r}")
    if str(new.get("group")) != "default":
        raise RuntimeError(f"new channel group wrong: {new.get('group')!r}")
    if int(new.get("priority") or 0) != NEW_CHANNEL_PRIORITY:
        raise RuntimeError(f"new channel priority wrong: {new.get('priority')!r}")
    if int(new.get("weight") or 0) != NEW_CHANNEL_WEIGHT:
        raise RuntimeError(f"new channel weight wrong: {new.get('weight')!r}")
    row_new = db_row(new_id, MODEL)
    if row_new != ("default", 1, NEW_CHANNEL_PRIORITY, NEW_CHANNEL_WEIGHT):
        raise RuntimeError(f"new channel abilities row wrong: {row_new}")
    ratios = json.loads(get_option_db(DB_PATH, MODEL_RATIO_OPTION))
    if ratios.get(MODEL) != 0:
        raise RuntimeError(f"ModelRatio for {MODEL} != 0 on readback: {ratios.get(MODEL)!r}")


def main() -> int:
    args = parse_args()
    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    channels = fetch_channels(smoke, headers)
    by_id = {int(c["id"]): c for c in channels}
    if CH_KEY_SOURCE not in by_id:
        raise RuntimeError(f"ch{CH_KEY_SOURCE} (key donor) missing from gateway")
    if any(str(c.get("name")) == NEW_CHANNEL_NAME for c in channels):
        print(f"channel {NEW_CHANNEL_NAME!r} already exists; nothing to do")
        return 0
    base_url = str(by_id[CH_KEY_SOURCE].get("base_url") or "").rstrip("/")
    current_ratio = get_option_db(DB_PATH, MODEL_RATIO_OPTION)
    print(f"plan: POST {NEW_CHANNEL_NAME!r} base_url={base_url} key=ch{CH_KEY_SOURCE} "
          f"models={MODEL} prio={NEW_CHANNEL_PRIORITY} weight={NEW_CHANNEL_WEIGHT} "
          f"UA header (browser)")
    print(f"plan: ModelRatio {MODEL} -> 0 (Go flat subscription, marginal cost zero; "
          f"current value {json.loads(current_ratio).get(MODEL, '<absent>')!r})")
    print(f"plan: ch{CH_KEY_SOURCE} unchanged (key donor only)")

    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    payload = {
        "name": NEW_CHANNEL_NAME,
        "type": 1,
        "base_url": base_url,
        "key": db_key(CH_KEY_SOURCE),
        "models": MODEL,
        "group": "default",
        "priority": NEW_CHANNEL_PRIORITY,
        "weight": NEW_CHANNEL_WEIGHT,
        "auto_ban": 1,
        "test_model": MODEL,
        "header_override": json.dumps({"User-Agent": BROWSER_UA}, separators=(",", ":")),
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

        put_option(smoke, headers, MODEL_RATIO_OPTION,
                   merge_ratio(current_ratio, MODEL, 0))
        print(f"ModelRatio: {MODEL} -> 0 merged")

        wait_for_abilities_row(new_id)
        row_new = db_row(new_id, MODEL)
        print(f"abilities: ch{new_id} row {row_new}")

        readback = fetch_channels(smoke, headers)
        verify(readback, new_id)
        print("verify ok: API readback + abilities row + ModelRatio=0 (ch101 untouched)")

        distribution = functional_test(read_gateway_key())
        print(f"functional ok: {FUNCTIONAL_REQUESTS} requests, "
              f"channel distribution: {distribution}")
        if distribution.get(new_id, 0) < FUNCTIONAL_REQUESTS:
            print(f"WARNING: expected all {FUNCTIONAL_REQUESTS} on ch{new_id} "
                  f"(only source); got {distribution}")
        print(f"OK: {MODEL} live on ch{new_id} via {base_url}; "
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