#!/usr/bin/env python3
"""Register a gifted Zhipu BigModel trial key as a last-tier glm-5.3 fallback.

The credential lives only in ~/.new-api-local/bigmodel-gift-key.txt and is never
printed; every report uses length + sha256 prefix.

Channel type is 26 (Zhipu V4), not 1.  This fork builds type=1 URLs as
``{base_url}/v1/chat/completions`` -- proven live by ch205 (step_plan), whose
base_url has no version segment.  BigModel's OpenAI-compatible path is
``/api/paas/v4/chat/completions``, so a type=1 row would be asked to reach
``/api/paas/v1/...``.  Type 26 carries the versioned path in the binary
(``relay/channel/zhipu_4v`` + the ``/api/paas/v4/chat/completions`` string), so
base_url stays empty and the adaptor supplies the endpoint itself.

Only glm-5.3 and glm-5.3-flash are declared, because those are the two names
verified against upstream; the key lists nine more, none probed.  Neither name
is exclusive to this channel, so the card expiring cannot strand a model.

The card expires and its balance is unreadable (BigModel exposes no quota
endpoint), so it is pinned to priority 20 / weight 1 -- below every currently
enabled glm-5.3 row (ch140-146 at 40, ch203 at 30, ch118 at 25) -- and
auto_ban=0, with the channel id added to Guardian's recovery-exclusion set so a
dead gift key never burns a probe again.

Default is a dry run.  --apply snapshots the DB, creates the channel disabled,
runs one management probe, waits for the channel cache, then enables.
--teardown reverses it.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
KEY_PATH = Path.home() / ".new-api-local" / "bigmodel-gift-key.txt"

CHANNEL_NAME = "bigmodel-glm53-gift"
CHANNEL_TYPE = 26  # Zhipu V4: versioned path is compiled into the adaptor
BASE_URL = ""  # empty -> adaptor uses https://open.bigmodel.cn/api/paas/v4
GROUP = "default"
PRIORITY = 20
WEIGHT = 1
AUTO_BAN = 0
CACHE_SYNC_SECONDS = 75
MODELS = ["glm-5.3", "glm-5.3-flash"]
TEST_MODEL = "glm-5.3"


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="make the backed-up live change; default is read-only")
    parser.add_argument("--teardown", action="store_true",
                        help="delete the channel this script owns")
    return parser.parse_args()


def upstream_key() -> str:
    key = KEY_PATH.read_text(encoding="utf-8").strip()
    if len(key) < 20 or "." not in key:
        raise RuntimeError(f"{KEY_PATH.name} does not look like a BigModel key")
    return key


def redact(key: str) -> str:
    return f"len={len(key)} sha12={hashlib.sha256(key.encode()).hexdigest()[:12]}"


def list_channels(smoke, headers: dict[str, str]) -> list[dict]:
    status, body = smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=300", headers=headers)
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"channel list failed: HTTP {status}")
    items = body.get("data") or []
    if isinstance(items, dict):
        items = items.get("items") or []
    return [i for i in items if isinstance(i, dict)]


def find_channel(smoke, headers: dict[str, str]) -> dict | None:
    hits = [c for c in list_channels(smoke, headers) if c.get("name") == CHANNEL_NAME]
    if len(hits) > 1:
        raise RuntimeError(f"multiple {CHANNEL_NAME} channels; refusing ambiguous cutover")
    return hits[0] if hits else None


def channel_payload(key: str) -> dict:
    return {
        "name": CHANNEL_NAME,
        "type": CHANNEL_TYPE,
        "key": key,
        "base_url": BASE_URL,
        "models": ",".join(MODELS),
        "group": GROUP,
        "test_model": TEST_MODEL,
        "priority": PRIORITY,
        "weight": WEIGHT,
        "status": 2,  # created disabled; enabled only after the probe
        "auto_ban": AUTO_BAN,
        "model_mapping": "",
    }


def create_channel(smoke, headers: dict[str, str], key: str) -> int:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/", method="POST",
        body={"mode": "multi_to_single", "channel": channel_payload(key)}, headers=headers)
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"channel POST failed: HTTP {status} {str(body)[:200]}")
    created = find_channel(smoke, headers)
    if not created or not isinstance(created.get("id"), int):
        raise RuntimeError(f"{CHANNEL_NAME} missing/ambiguous on readback")
    return int(created["id"])


def set_status(smoke, headers: dict[str, str], channel_id: int, status: int) -> None:
    response_status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}/status", method="POST",
        body={"status": status}, headers=headers)
    if response_status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"status POST failed: HTTP {response_status} {str(body)[:160]}")


def delete_channel(smoke, headers: dict[str, str], channel_id: int) -> None:
    response_status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}", method="DELETE", headers=headers)
    if response_status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"channel DELETE failed: HTTP {response_status}")


def management_probe(smoke, headers: dict[str, str], channel_id: int, model: str) -> bool:
    """One probe, no retries: the management path is the only call that exercises
    this fork's URL building, key, and format conversion together, and burst
    short-prompt probing is what got ch206 banned upstream."""
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={model}", headers=headers, timeout=170)
    text = json.dumps(body, ensure_ascii=False) if isinstance(body, (dict, list)) else str(body)
    ok = status == 200 and isinstance(body, dict) and bool(body.get("success"))
    print(f"  probe {model:<16} {'ok' if ok else 'FAIL'} "
          f"time={body.get('time') if isinstance(body, dict) else '-'} "
          f"{'' if ok else text[:200]}")
    return ok


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    destination = backup_dir / f"new-api-before-{CHANNEL_NAME}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    if destination.exists():
        raise RuntimeError("backup destination exists; refusing to overwrite")
    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    with closing(sqlite3.connect(f"file:{destination.as_posix()}?mode=ro", uri=True)) as con:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("backup integrity check failed")
    return destination


def pool_state(db_path: Path) -> tuple[dict[str, list[tuple]], list[str]]:
    """Enabled glm-5.3 pool rows plus any ratio gap for the declared names."""
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        ratios = {}
        for option in ("ModelRatio", "CompletionRatio"):
            row = con.execute("SELECT value FROM options WHERE key = ?", (option,)).fetchone()
            ratios[option] = json.loads(row[0]) if row and row[0] else {}
        pools = {}
        for model in MODELS:
            rows = con.execute(
                'SELECT a.channel_id, c.name, a.priority, a.weight, c.status FROM abilities a '
                'JOIN channels c ON c.id = a.channel_id '
                'WHERE a.model = ? AND a."group" = ? AND a.enabled = 1 AND c.status = 1 '
                'ORDER BY a.priority DESC', (model, GROUP)).fetchall()
            pools[model] = [tuple(r) for r in rows]
    missing = [m for m in MODELS if m not in ratios["ModelRatio"] or m not in ratios["CompletionRatio"]]
    return pools, missing


def assert_prerequisites(db_path: Path) -> None:
    pools, missing = pool_state(db_path)
    if missing:
        raise RuntimeError(f"ratios missing for {missing}; an unregistered name bills at implied ratio 37.5")
    for model, rows in pools.items():
        if not rows:
            raise RuntimeError(f"{model} has no enabled pool; this channel would be the only route")
        top = max(r[2] for r in rows)
        lowest = min(r[2] for r in rows)
        if PRIORITY > lowest:
            raise RuntimeError(f"{model}: priority {PRIORITY} would outrank live row {lowest} (ch{rows[-1][0]})")
        print(f"  {model} pool: {len(rows)} enabled channels, priorities {top}..{lowest}, "
              f"new channel joins at {PRIORITY} (last tier, weight {WEIGHT})")
        if any(r[2] <= PRIORITY for r in rows):
            print(f"    NOTE: {model} already has rows at/below {PRIORITY}; this key shares the last tier")


def abilities_rows(db_path: Path, channel_id: int) -> list[tuple]:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        return [tuple(r) for r in con.execute(
            'SELECT model, "group", enabled, priority, weight FROM abilities '
            'WHERE channel_id = ? ORDER BY model', (channel_id,)).fetchall()]


def key_fingerprint(db_path: Path, channel_id: int) -> tuple[int, str]:
    """Length + digest of the stored key, never the key itself.  The management
    API redacts keys in this fork, so proving the status write left the
    credential alone requires reading it in the database."""
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        row = con.execute("SELECT key FROM channels WHERE id = ?", (channel_id,)).fetchone()
    key = str((row or [None])[0] or "")
    return len(key), hashlib.sha256(key.encode()).hexdigest()[:12]


def main() -> int:
    args = parse_args()
    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}
    key = upstream_key()

    existing = find_channel(smoke, headers)
    channel_id = int(existing["id"]) if existing else None

    if args.teardown:
        if channel_id is None:
            print(f"no {CHANNEL_NAME} channel to tear down")
            return 0
        if not args.apply:
            print(f"dry-run: would DELETE ch{channel_id}")
            return 0
        backup = online_backup(db_path)
        print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")
        delete_channel(smoke, headers, channel_id)
        print(f"OK: ch{channel_id} deleted. Recovery snapshot: {backup.name}")
        print("TODO: drop the now-dangling id from guardian.py AUTO_BAN_RECOVERY_EXCLUSIONS")
        return 0

    if existing and (existing.get("type") != CHANNEL_TYPE or existing.get("group") != GROUP
                     or str(existing.get("base_url") or "") != BASE_URL):
        raise RuntimeError("existing channel no longer matches the inspected contract; refusing to reuse it")

    print(f"plan: {'set ch' + str(channel_id) if channel_id else 'create ' + CHANNEL_NAME}; "
          f"type={CHANNEL_TYPE} (Zhipu V4, empty base_url) models={MODELS} group={GROUP} "
          f"priority={PRIORITY} weight={WEIGHT} auto_ban={AUTO_BAN}; key {redact(key)} (value never printed)")
    assert_prerequisites(db_path)

    if not args.apply:
        print("dry-run: no write performed. Re-run with --apply.")
        return 0

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    if channel_id is None:
        channel_id = create_channel(smoke, headers, key)
        print(f"created ch{channel_id} disabled")
    before = key_fingerprint(db_path, channel_id)
    if before[0] == 0:
        raise RuntimeError(f"ch{channel_id} stored key is empty; refusing to probe/enable")
    print(f"  stored key fingerprint: len={before[0]} sha12={before[1]} (must match {redact(key)})")
    if before[1] != redact(key).split("sha12=")[1]:
        print("  WARN: stored key differs from the key file; the row was written by another actor")

    if not management_probe(smoke, headers, channel_id, TEST_MODEL):
        print(f"ABORT: management probe failed; ch{channel_id} stays disabled (no live traffic added)")
        return 1

    print(f"  waiting {CACHE_SYNC_SECONDS}s for the channel cache, then enabling")
    time.sleep(CACHE_SYNC_SECONDS)
    set_status(smoke, headers, channel_id, 1)
    after = key_fingerprint(db_path, channel_id)
    if after != before:
        print(f"  WARN: key changed during the status write: {before} -> {after}")
    rows = abilities_rows(db_path, channel_id)
    print(f"  abilities ch{channel_id}: {rows}")
    expected = {m for m in MODELS}
    got = {r[0] for r in rows}
    if got != expected:
        print(f"  FAIL: expected models {sorted(expected)}, got {sorted(got)}")
        return 1
    if any(r[2] != 1 for r in rows):
        print(f"  FAIL: an abilities row is still disabled: {rows}")
        return 1
    status_row = next((c for c in list_channels(smoke, headers) if c.get("id") == channel_id), None)
    print(f"  live channel: status={status_row.get('status')} priority={status_row.get('priority')} "
          f"weight={status_row.get('weight')} auto_ban={status_row.get('auto_ban')} "
          f"test_model={status_row.get('test_model')}")
    print(f"OK: ch{channel_id} enabled at last tier. Rollback: --teardown --apply, "
          f"or restore {backup.name}")
    print(f"NEXT: add {channel_id} to guardian.py AUTO_BAN_RECOVERY_EXCLUSIONS so a dead gift key burns no probes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
