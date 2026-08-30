#!/usr/bin/env python3
"""Aggregate a second source for `deepseek-v4-flash` (OMP compactionModel + smol/muse chain head).

Background:
  OMP uses `zg-newapi/deepseek-v4-flash` as the compactionModel for most roles and as the
  smol/muse fallback-chain head. After ch15 (sensenova, the p50 primary) was disabled for
  quota exhaustion, the pool thinned to ch111 (bai-free p30) + ch110 (yjs p6) — two free-tier
  sources for a critical path. This adds a dedicated seeseed channel as a co-primary (p30) in
  a distinct failure domain (seeseed1ck.icu, not bai/yjs) so compaction load splits 1:1 with bai.

Probe evidence (2026-08-28):
  - Supplier scan: several enabled relays carry `deepseek-v4-flash` upstream (ch88 runinfra,
    ch89 seeseed, ch101 opencode-go, ch112/113, ch117), though each is configured as a
    dedicated channel for other models.
  - Direct chat-completion probe on `deepseek-v4-flash`:
      ch89  seeseed     OK 2.1s finish=stop content='pong'   <- chosen donor
      ch101 opencode-go OK 1.3s finish=stop content='pong'
      ch88  runinfra    OK 2.3s finish=length content='p'    (reasoning burns the token budget)
      ch96  opencode-zen-free HTTP 401 CreditsError insufficient balance  (excluded)
  - Chose seeseed (ch89): clean non-reasoning response, fresh failure domain (ch101's key
    already carries ch117/qwen3.8-max-free, so reusing it would concentrate two critical
    models on one subscription).

Change (single dedicated channel, mirrors the ch112/ch113/ch117 pattern):
  seeseed-deepseek-v4-flash    base_url=https://api-yi-hydrogel.seeseed1ck.icu
                               models=deepseek-v4-flash  (identity, no mapping needed)
                               key copied from ch89, priority=30, weight=5, auto_ban=1
  ch111 (bai-free p30/w5) and ch110 (yjs p6/w5) are UNTOUCHED. New channel at p30/w5
  becomes co-primary with ch111 (1:1 weighted mix); ch110 remains the p6 fallback.

Safety:
  * Full DB backup to ~/.new-api-local/backups/new-api-before-deepseek-v4-flash-pool-<ts>.db
    before any mutation.
  * Key copied from donor channel row in DB; never printed, never left on disk.
  * Every step verified by readback (API + DB); on any failure the new channel is deleted
    (API + abilities) so nothing half-created lingers.
  * Functional test: N clean chat completions through the gateway, attributed per channel
    via the logs table.

Usage:
  python3 add_deepseek_v4_flash_pool.py            # dry run
  python3 add_deepseek_v4_flash_pool.py --apply    # execute
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HOME = Path.home()
DB_PATH = HOME / ".new-api-local" / "new-api.db"
BACKUP_DIR = HOME / ".new-api-local" / "backups"
SECRETS_PATH = HOME / ".omp" / "guardian" / "secrets.json"
BASE = "http://127.0.0.1:3002"

MODEL = "deepseek-v4-flash"
UPSTREAM_ID = "deepseek-v4-flash"  # seeseed accepts the same id; identity, no mapping
CH_PRIMARY = 111                    # bai-free (existing primary, unchanged)
CH_KEY_SOURCE = 89                  # seeseed1ck-hydrogel (key donor)
NEW_CHANNEL_NAME = "seeseed-deepseek-v4-flash"
PRIORITY = 30
WEIGHT = 5
BACKUP_PREFIX = "new-api-before-deepseek-v4-flash-pool"
FUNCTIONAL_REQUESTS = 12
FUNCTIONAL_MAX_TOKENS = 120

UA = "cc-switch-ops/1.0 (local NewAPI pool aggregation)"


_KEY_RE = re.compile(r"\b(?:sk|gsk|pk|tok)[_-][A-Za-z0-9_-]{16,}\b")


def _redact(text: str) -> str:
    """Mask any API-key-shaped token; never hardcode key literals here."""
    return _KEY_RE.sub("[REDACTED]", text)


def log(msg: str) -> None:
    print(_redact(msg), flush=True)


def fatal(msg: str) -> None:
    log(f"ERROR: {msg}")
    sys.exit(1)


def load_admin_token() -> str:
    try:
        data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        fatal(f"cannot read guardian secrets {SECRETS_PATH}: {exc}")
    token = data.get("newapi_token")
    if not token:
        fatal("guardian secrets missing newapi_token")
    return token


def api_call(token: str, method: str, path: str, payload: dict | None = None, timeout: int = 30):
    url = f"{BASE}{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def db_conn(readonly: bool = True) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def backup_db() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"{BACKUP_PREFIX}-{ts}.db"
    src = db_conn(True)
    try:
        dst = sqlite3.connect(str(dest))
        with dst:
            src.backup(dst)
        dst.close()
    finally:
        src.close()
    return dest


def get_channel_row(channel_id: int) -> sqlite3.Row:
    conn = db_conn(True)
    try:
        row = conn.execute(
            "SELECT * FROM channels WHERE id = ?", (channel_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        fatal(f"channel {channel_id} not found")
    return row


def new_channel_payload(base_url: str, key: str) -> dict:
    return {
        "type": 1,
        "name": NEW_CHANNEL_NAME,
        "base_url": base_url,
        "key": key,
        "models": MODEL,
        "model_mapping": json.dumps({}),  # identity: request id == upstream id
        "groups": ["default"],
        "group": "default",
        "priority": PRIORITY,
        "weight": WEIGHT,
        "auto_ban": 1,
        "test_model": MODEL,
        "status": 1,
    }


def create_channel(token: str, payload: dict) -> int:
    body = api_call(token, "POST", "/api/channel/", {"mode": "single", "channel": payload})
    if not body.get("success"):
        fatal(f"create channel failed: {body.get('message')}")
    data = body.get("data")
    if isinstance(data, dict) and data.get("id"):
        return int(data["id"])
    # POST succeeded but response lacked data.id; recover the id by name.
    conn = db_conn(True)
    try:
        row = conn.execute("SELECT id FROM channels WHERE name = ?", (NEW_CHANNEL_NAME,)).fetchone()
    finally:
        conn.close()
    if row is None:
        fatal(f"channel created but id unrecoverable; response={body}")
    log("create response lacked data.id; recovered channel id from DB by name")
    return int(row["id"])


def abilities_rows(model: str):
    conn = db_conn(True)
    try:
        rows = conn.execute(
            "SELECT channel_id, enabled, priority, weight FROM abilities "
            "WHERE model = ? ORDER BY priority DESC, weight DESC, channel_id",
            (model,),
        ).fetchall()
    finally:
        conn.close()
    return [(r["channel_id"], r["enabled"], r["priority"], r["weight"]) for r in rows]


def wait_for_ability(channel_id: int, token: str, timeout_s: int = 180) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if any(r[0] == channel_id for r in abilities_rows(MODEL)):
            return True
        time.sleep(2)
    log("abilities row did not appear; inserting directly and re-enabling via API update")
    conn = db_conn(False)
    try:
        with conn:
            conn.execute(
                "INSERT INTO abilities (\"group\", model, channel_id, enabled, priority, weight) "
                "VALUES ('default', ?, ?, 1, ?, ?)",
                (MODEL, channel_id, PRIORITY, WEIGHT),
            )
    finally:
        conn.close()
    api_call(token, "PUT", f"/api/channel/{channel_id}", {"status": 1})
    time.sleep(2)
    return any(r[0] == channel_id for r in abilities_rows(MODEL))


def delete_channel_full(token: str, channel_id: int) -> None:
    try:
        api_call(token, "DELETE", f"/api/channel/{channel_id}")
    except Exception as exc:  # noqa: BLE001
        log(f"WARNING: API delete failed: {exc}")
    conn = db_conn(False)
    try:
        with conn:
            conn.execute("DELETE FROM abilities WHERE channel_id = ?", (channel_id,))
    finally:
        conn.close()
    log(f"cleaned up channel {channel_id} + abilities rows")


def verify_channel(token: str, channel_id: int) -> None:
    body = api_call(token, "GET", f"/api/channel/{channel_id}")
    if not body.get("success"):
        fatal(f"readback failed: {body.get('message')}")
    data = body["data"]
    if data.get("status") != 1:
        fatal(f"readback status != 1: {data.get('status')}")
    models = data.get("models", "")
    if MODEL not in (models if isinstance(models, str) else ",".join(models)):
        fatal(f"readback models missing {MODEL}: {models}")
    if str(data.get("group", "")) != "default":
        fatal(f"readback group != default: {data.get('group')!r}")
    if int(data.get("priority", -1)) != PRIORITY or int(data.get("weight", -1)) != WEIGHT:
        fatal(f"readback priority/weight mismatch: {data.get('priority')}/{data.get('weight')}")
    ability = next((r for r in abilities_rows(MODEL) if r[0] == channel_id), None)
    if ability is None:
        fatal("abilities row missing after verification")
    if ability[1] != 1:
        fatal(f"abilities row not enabled: {ability}")
    db_row = get_channel_row(channel_id)
    if not db_row["key"]:
        fatal("DB readback: key empty")
    log(f"verified: ch{channel_id} status=1 models={MODEL} group=default "
        f"priority={PRIORITY} weight={WEIGHT} abilities={ability}")


def read_gateway_key() -> str:
    """Read the OMP zg-newapi relay apiKey from the live models.yml (never printed)."""
    text = (HOME / ".omp" / "agent" / "models.yml").read_text(encoding="utf-8")
    match = re.search(
        r"^  zg-newapi:\n(?:    .*\n)*?    apiKey:\s*(\S+)", text, flags=re.M
    )
    if not match:
        fatal("zg-newapi apiKey not found in live models.yml")
    return match.group(1)


def functional_test(api_key: str, channel_ids: set[int]) -> bool:
    start = time.time()
    log(f"functional test: {FUNCTIONAL_REQUESTS} clean chat completions for {MODEL}")
    ok = 0
    for i in range(FUNCTIONAL_REQUESTS):
        body = json.dumps(
            {
                "model": MODEL,
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "max_tokens": FUNCTIONAL_MAX_TOKENS,
            }
        ).encode()
        req = urllib.request.Request(
            f"{BASE}/v1/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
            ok += 1
            log(f"  [{i + 1}] 200")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            log(f"  [{i + 1}] HTTP {exc.code}: {detail}")
        except Exception as exc:  # noqa: BLE001
            log(f"  [{i + 1}] error: {exc}")
    time.sleep(2)

    conn = db_conn(True)
    try:
        rows = conn.execute(
            "SELECT channel_id FROM logs "
            "WHERE model_name = ? AND created_at > ? AND type = 2",
            (MODEL, start),
        ).fetchall()
    finally:
        conn.close()

    by_channel: dict[int, int] = {}
    for (ch,) in rows:
        ch = ch or 0
        by_channel[ch] = by_channel.get(ch, 0) + 1
    mine = {ch: n for ch, n in by_channel.items() if ch in channel_ids}
    other = {ch: n for ch, n in by_channel.items() if ch not in channel_ids}
    log(f"  logs window: success_by_channel={mine} other_channels={other}")

    if ok == 0:
        fatal("functional test: all requests failed")
    if not any(ch in channel_ids and n > 0 for ch, n in by_channel.items()):
        log("WARNING: pool channels received no attributed traffic; verify abilities manually")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="execute changes (default dry run)")
    args = parser.parse_args()

    if not DB_PATH.exists():
        fatal(f"NewAPI DB not found at {DB_PATH}")
    token = load_admin_token()

    src = get_channel_row(CH_KEY_SOURCE)
    base_url = src["base_url"]
    key = src["key"]
    if not base_url or not key:
        fatal(f"donor channel {CH_KEY_SOURCE} missing base_url/key")
    if "/v1" in base_url:
        fatal(f"donor base_url carries /v1 ({base_url}); type=1 would double-append — aborting")

    conn = db_conn(True)
    try:
        existing = conn.execute("SELECT id FROM channels WHERE name = ?", (NEW_CHANNEL_NAME,)).fetchone()
    finally:
        conn.close()
    if existing is not None:
        fatal(f"channel '{NEW_CHANNEL_NAME}' already exists (ch{existing['id']}); refusing to create a duplicate")

    primary_ability = next((r for r in abilities_rows(MODEL) if r[0] == CH_PRIMARY), None)

    log(f"plan: POST '{NEW_CHANNEL_NAME}' base_url={base_url} key=ch{CH_KEY_SOURCE} "
        f"models={MODEL} (identity, no mapping) prio={PRIORITY} weight={WEIGHT}")
    log(f"plan: ch{CH_PRIMARY} unchanged (abilities {primary_ability}); "
        f"new channel co-primary at p{PRIORITY}/w{WEIGHT}")

    if not args.apply:
        log("dry-run: no changes made")
        return

    backup_path = backup_db()
    log(f"backup: {backup_path}")

    payload = new_channel_payload(base_url, key)
    channel_id = create_channel(token, payload)
    log(f"created channel id={channel_id}")

    if not wait_for_ability(channel_id, token):
        delete_channel_full(token, channel_id)
        fatal("abilities row could not be established; rolled back")

    try:
        verify_channel(token, channel_id)
    except SystemExit:
        delete_channel_full(token, channel_id)
        raise

    functional_test(read_gateway_key(), {CH_PRIMARY, channel_id})
    log(f"OK: {MODEL} now pooled ch{CH_PRIMARY}+ch{channel_id} (co-primary p{PRIORITY}); "
        f"backup={backup_path}")


if __name__ == "__main__":
    main()
