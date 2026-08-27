#!/usr/bin/env python3
"""Pool qwen3-8-27b: ch88 (runinfra) + new dedicated channel on api.yjs.im
(2026-08-28).

Background
----------
The OMP default model ``zg-newapi/qwen3-8-27b`` (roles default/commit/smol,
extension compaction candidate #4) was a single point of failure on
ch88 ``runinfra-qwen3-8-27b`` (one key, one relay).

Supplier scan (2026-08-28) found a second live source with an existing key:

- ``https://api.yjs.im`` (ch110 ``yjs-free`` key) serves upstream model id
  ``qwen-3.8-27b`` — the same open-weight release (HuggingFace
  ``Qwen/Qwen3.8-27B``) under different relay naming as runinfra's
  ``qwen3-8-27b``. Verified live: ``GET /v1/models`` lists it and a chat
  completion returns ``model=qwen-3.8-27b`` (reasoning model; content lands
  in ``message.reasoning`` first).
- OpenRouter (``qwen/qwen3.8-27b``) and HF inference also serve it but need
  new accounts/keys — not used.

Mechanism finding (why a NEW channel, not extending ch110)
----------------------------------------------------------
Empirical (2026-08-28): the ``abilities`` table is a derived artifact of the
channel row — row existence mirrors ``channels.models`` and
``(priority, weight)`` mirror the channel-level ``priority``/``weight``; the
sync goroutine re-derives rows after channel API events (a direct SQL UPDATE
of ch88's row to 50/50 was reverted to the channel's 0/1 within seconds, and
the orphaned row from a rolled-back ch110 model add was removed by the
goroutine after the channel PUT restore). ch110 hosts 19 models, so its
channel-level weight cannot be raised for one model without perturbing every
pool it participates in (deepseek-v4-flash, glm-5.2, k3, …). Therefore the
second source is a **dedicated single-model channel** (the same pattern as
ch88 itself): own key entry, own weight vector, zero entanglement.

Change
------
1. POST new channel ``yjs-qwen3-8-27b`` (坑1: ``{"mode":"single","channel":
   {...}}`` wrapper, no ``status`` field): base_url ``https://api.yjs.im``,
   key copied from ch110, type=1, models ``qwen3-8-27b``, mapping
   ``qwen3-8-27b -> qwen-3.8-27b``, group ``default``, priority=0, weight=1,
   auto_ban=1, test_model ``qwen3-8-27b``.
2. No ch88 change: its channel-level fields (priority=0, weight=1) already
   produce a 1:1 weighted mix with the new channel at equal priority; ch88
   is single-model so its fields stay entangled with nothing else.
3. No OMP change: OMP keeps calling model id ``qwen3-8-27b``; NewAPI
   load-balances by weight at equal priority, auto_ban (both channels
   auto_ban=1) routes around outages/rate limits.

No ModelRatio change: ``qwen3-8-27b`` is absent from the 193-entry ModelRatio
blob and ch88 already runs on the default 1.0 since 2026-08-27.

Safety: online DB snapshot first, API+DB readback verify, 12-request gateway
functional test with channel distribution from the ``logs`` table, automatic
channel DELETE + abilities-row cleanup on failure. Dry-run by default.
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

MODEL = "qwen3-8-27b"
UPSTREAM_ID = "qwen-3.8-27b"
CH_PRIMARY = 88        # runinfra-qwen3-8-27b (existing single-model channel)
CH_KEY_SOURCE = 110    # yjs-free (key donor; NOT modified)
NEW_CHANNEL_NAME = "yjs-qwen3-8-27b"
NEW_CHANNEL_PRIORITY = 0
NEW_CHANNEL_WEIGHT = 1
SYNC_POLL_SECONDS = 180
SYNC_POLL_INTERVAL = 5
FUNCTIONAL_REQUESTS = 12

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
        f"new-api-before-qwen38-27b-pool-{time.strftime('%Y%m%d-%H%M%S')}.db"
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
    without commit rolls the DML back — caught 2026-08-28 on the ch110 plan)."""
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
            with urllib.request.urlopen(req, timeout=60) as resp:
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
    if CH_PRIMARY not in by_id:
        raise RuntimeError(f"ch{CH_PRIMARY} missing from readback")
    new = by_id.get(new_id)
    if new is None:
        raise RuntimeError(f"new channel {NEW_CHANNEL_NAME} (id={new_id}) missing from readback")
    if str(new.get("models")) != MODEL:
        raise RuntimeError(f"new channel models wrong: {new.get('models')!r}")
    mapping_raw = str(new.get("model_mapping") or "")
    mapping = json.loads(mapping_raw) if mapping_raw.strip() else {}
    if mapping.get(MODEL) != UPSTREAM_ID:
        raise RuntimeError(f"new channel mapping wrong: {mapping}")
    if str(new.get("group")) != "default":
        raise RuntimeError(f"new channel group wrong: {new.get('group')!r}")
    row_primary = db_row(CH_PRIMARY, MODEL)
    row_new = db_row(new_id, MODEL)
    if row_primary != ("default", 1, 0, 1):
        raise RuntimeError(f"ch{CH_PRIMARY} abilities row changed unexpectedly: {row_primary}")
    if row_new != ("default", 1, NEW_CHANNEL_PRIORITY, NEW_CHANNEL_WEIGHT):
        raise RuntimeError(f"new channel abilities row wrong: {row_new}")


def main() -> int:
    args = parse_args()
    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    channels = fetch_channels(smoke, headers)
    by_id = {int(c["id"]): c for c in channels}
    if CH_PRIMARY not in by_id:
        raise RuntimeError(f"ch{CH_PRIMARY} missing from gateway")
    if CH_KEY_SOURCE not in by_id:
        raise RuntimeError(f"ch{CH_KEY_SOURCE} (key donor) missing from gateway")
    if any(str(c.get("name")) == NEW_CHANNEL_NAME for c in channels):
        print(f"channel {NEW_CHANNEL_NAME!r} already exists; nothing to do")
        return 0
    row_primary = db_row(CH_PRIMARY, MODEL)
    base_url = str(by_id[CH_KEY_SOURCE].get("base_url") or "").rstrip("/")
    print(f"plan: POST {NEW_CHANNEL_NAME!r} base_url={base_url} key=ch{CH_KEY_SOURCE} "
          f"models={MODEL} mapping {MODEL}->{UPSTREAM_ID} prio={NEW_CHANNEL_PRIORITY} "
          f"weight={NEW_CHANNEL_WEIGHT}")
    print(f"plan: ch{CH_PRIMARY} unchanged (abilities {row_primary}; single-model channel, "
          f"1:1 weighted mix at equal priority)")

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
        print("verify ok: API readback + abilities rows (ch88 untouched)")

        distribution = functional_test(read_gateway_key())
        print(f"functional ok: {FUNCTIONAL_REQUESTS} requests, "
              f"channel distribution: {distribution}")
        if new_id not in distribution:
            print("WARNING: new channel did not receive any of the 12 requests "
                  "(1:1 weighted makes this ~0.02%); observe logs before assuming a fault")
        print(f"OK: qwen3-8-27b now pooled ch{CH_PRIMARY}+ch{new_id} 1:1; "
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
