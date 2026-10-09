#!/usr/bin/env python3
"""Onboard http://103.39.64.76:39112 (raw-IP relay, glm-5.3-flash) as a backup-tier
channel in local NewAPI (2026-10-07).

Why a backup tier and not a primary:
  `glm-5.3-flash` has exactly one enabled carrier today — ch149 nimbridge-relay,
  and that one is a LOCAL BRIDGE (http://127.0.0.1:8791). If the bridge process
  dies the model has zero carriers. This relay is direct-egress and serves the
  exact same model id, so it is pure failover value.

Posture (verified against live state, not assumed):
  - NewAPI priority: larger value wins. Evidence: `deepseek-v4-flash` enabled on
    ch15 (p50, 1462 hits) and ch149 (p-20, 183 hits) -> ch15 dominates; and the
    hubway backup (-20) sits below primaries at 40/-10 by the same rule.
  - Enabled carriers for glm-5.3-flash: only ch149 (-20, w1). Disabled: ch121
    bai (30) and the tokenrhythm pool k01..k21 (-30). PRIORITY = -30 therefore
    sits strictly below every ENABLED carrier -> this channel cannot win traffic
    while ch149 is up, and takes over when it is not.

Safety contract:
  - dry-run by default; key comes from the environment (never written to disk/log).
  - online DB backup with integrity check before any write.
  - created DISABLED (status=2) and only enabled after abilities + upstream
    probe pass; any later assertion failure re-disables the channel (rollback).
  - idempotent resume with drift detection (never silently overwrite a drifted channel).
  - pricing is a read-only parity report (ratios are never overwritten).

Run:
  RELAY_103_39_64_76_KEY=... python scripts/ops/add_103_39_64_76_relay_channel.py --apply
"""
from __future__ import annotations

import argparse
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
DB_PATH = Path.home() / ".new-api-local" / "new-api.db"
GATEWAY_BASE = "http://127.0.0.1:3002"

# base_url WITHOUT /v1: NewAPI auto-appends /v1/chat/completions
BASE_URL = "http://103.39.64.76:39112"
CHANNEL_NAME = "103.39.64.76-relay"
MODELS = ["glm-5.3-flash"]
MODEL_MAPPING: dict[str, str] = {}  # upstream serves the exact same id
TEST_MODEL = "glm-5.3-flash"
# backup tier: strictly below the sole enabled carrier ch149 nimbridge (-20)
PRIORITY = -30
WEIGHT = 1
# the enabled primary that MUST keep the traffic while it is serving
PRIMARY_CHANNEL = 149
ENV_KEY = "RELAY_103_39_64_76_KEY"


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load smoke module from {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def db_one(sql, params=()):
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as c:
        return c.execute(sql, params).fetchone()


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = backup_dir / f"new-api-before-103-39-64-76-{stamp}.db"
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as src:
        with closing(sqlite3.connect(destination.as_posix())) as dst:
            src.backup(dst)
            integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"backup integrity_check={integrity}")
    return destination


def fetch_channels(smoke, headers):
    """All channels, paginated.

    The admin list endpoint silently caps `page_size` at 100 (observed
    2026-10-07: page_size=500 returned items=100 of total=103), so a single
    request misses newly created channels beyond the first page — iterate.
    """
    collected: dict[int, dict] = {}
    total = None
    for page in range(1, 21):
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/?p={page}&page_size=100", headers=headers
        )
        if status != 200 or not isinstance(body, dict) or not body.get("success"):
            raise RuntimeError(f"channel list failed: HTTP {status}")
        data = body.get("data") or {}
        items = [i for i in (data.get("items") or []) if isinstance(i, dict)]
        if total is None:
            total = data.get("total")
        for item in items:
            collected[int(item.get("id") or 0)] = item
        if not items or (isinstance(total, int) and len(collected) >= total):
            break
    if isinstance(total, int) and len(collected) < total:
        raise RuntimeError(f"channel pagination incomplete: {len(collected)}/{total}")
    return list(collected.values())


def read_gateway_key() -> str:
    """OMP zg-newapi apiKey from live models.yml (never printed)."""
    text = (Path.home() / ".omp" / "agent" / "models.yml").read_text(encoding="utf-8")
    match = re.search(r"zg-newapi.*?apiKey:\s*(\S+)", text, re.S)
    if not match:
        raise RuntimeError("zg-newapi apiKey not found in models.yml")
    return match.group(1)


def wait_abilities(channel_id: int, model: str, seconds: int = 90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        row = db_one(
            'SELECT "group", enabled, priority, weight FROM abilities WHERE channel_id = ? AND model = ?',
            (channel_id, model),
        )
        if row:
            return row
        time.sleep(3)
    return None


def upstream_probe_once(key: str) -> tuple[int, str]:
    """One minimal chat against the relay itself (preflight, before any write)."""
    payload = json.dumps(
        {
            "model": TEST_MODEL,
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": 128,
        }
    ).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/v1/chat/completions",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
            message = ((body.get("choices") or [{}])[0].get("message") or {})
            text = (message.get("content") or message.get("reasoning_content") or "").strip()
            return resp.status, text
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:200]


def upstream_probe(key: str, attempts: int = 8, pause: int = 10) -> tuple[int, str, int]:
    """Bounded-retry preflight; returns (last_status, last_text, successes).

    This relay's own upstream pool for glm-5.3-flash flaps: measured 2026-10-07
    2/6 chats HTTP 200 vs 4/6 HTTP 503 `model_not_found` / "No available channel
    for model glm-5.3-flash under group 贡献 (distributor)" — a 503 that
    NewAPI's AutomaticRetryStatusCodes covers, which is exactly why this channel
    is only ever a backup (never a primary carrier).
    """
    last_status, last_text, successes = 0, "", 0
    for attempt in range(1, attempts + 1):
        last_status, last_text = upstream_probe_once(key)
        print(f"  preflight attempt {attempt}/{attempts}: HTTP {last_status} "
              f"text={last_text[:60]!r}")
        if last_status == 200:
            successes += 1
            break
        if attempt < attempts:
            time.sleep(pause)
    return last_status, last_text, successes


def channel_status(channel_id: int):
    row = db_one("SELECT status FROM channels WHERE id = ?", (channel_id,))
    return int(row[0]) if row else None


def set_status(smoke, headers, channel_id: int, status: int) -> None:
    """Enable/disable a channel.

    Contract (Guardian + quarantine scripts + add_* scripts): the status toggle
    is `POST /api/channel/{id}/status {"status": N}` — a PUT to /api/channel/
    with a status field is rejected with 'Invalid parameters' (observed).
    """
    http_status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}/status",
        method="POST",
        body={"status": status},
        headers=headers,
    )
    if http_status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(
            f"channel {channel_id} status={status} failed: HTTP {http_status} message={message!r}"
        )
    live = channel_status(int(channel_id))
    if live != status:
        raise RuntimeError(f"ch{channel_id} status read-back {live} != requested {status}")


def gateway_chat(smoke, gateway_key: str, model: str, max_tokens: int = 64):
    """One gateway chat; returns (status, usage, attribution_channel_id)."""
    mark = time.time()
    status, body = smoke.http_json(
        f"{GATEWAY_BASE}/v1/chat/completions",
        method="POST",
        body={
            "model": model,
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": max_tokens,
        },
        headers={"Authorization": f"Bearer {gateway_key}"},
        timeout=180,
    )
    usage = (body or {}).get("usage") or {}
    attr = None
    for _ in range(6):
        row_log = db_one(
            "SELECT channel_id FROM logs WHERE model_name = ? AND created_at > ? AND type = 2 "
            "ORDER BY id DESC LIMIT 1",
            (model, mark),
        )
        if row_log:
            attr = int(row_log[0])
            break
        time.sleep(5)
    return status, usage, attr


def gateway_chat_retry(smoke, gateway_key: str, model: str, attempts: int = 4, pause: int = 20):
    """gateway_chat with bounded retries on the transient failure classes only."""
    last = None
    for i in range(1, attempts + 1):
        last = gateway_chat(smoke, gateway_key, model)
        status = last[0]
        if status == 200:
            return last
        if status in (404, 408, 429, 500, 502, 503, 504, 524) and i < attempts:
            print(f"    retry {model}: HTTP {status} (attempt {i}/{attempts})")
            time.sleep(pause)
            continue
        return last
    return last


def admin_channel_test(smoke, headers, channel_id: int, model: str):
    """Live upstream call through THIS channel's machinery (routing untouched).

    Time-spread budget (12 attempts / 15s): the upstream flaps hard — measured
    2026-10-07 windows of 0/5 and 2/6 successes — so a short gate would reject
    a channel that does serve. P(miss all) ~ 1-2% at a ~30% success rate.
    """
    last = (0, {})
    for attempt in range(1, 13):
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={model}",
            headers=headers,
            timeout=120,
        )
        last = (status, body)
        ok = isinstance(body, dict) and body.get("success")
        print(f"channel test: HTTP {status} success={ok} "
              f"time={(body or {}).get('time') if isinstance(body, dict) else None} "
              f"model={model} attempt={attempt}/12")
        if ok or attempt == 12:
            return last
        time.sleep(15)
    return last


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    args = parser.parse_args()

    print("plan:")
    print(f"  create: {CHANNEL_NAME} models={MODELS} prio={PRIORITY} w={WEIGHT} auto_ban=1")
    print(f"  base: {BASE_URL} (no /v1; NewAPI auto-appends)  mapping: none (exact id)")
    print(f"  posture: backup tier strictly below enabled primary ch{PRIMARY_CHANNEL} (-20) "
          f"-> must NOT win traffic while it serves")
    print("  flow: upstream preflight -> DB backup -> create DISABLED -> channel test (upstream "
          "proof, routing untouched) -> enable -> fix/abilities -> gateway attribution "
          "-> rollback (disable) on any post-enable failure")
    print("  pricing: read-only parity report (never overwrite ratios)")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    key = os.environ.get(ENV_KEY, "").strip()
    if not key:
        raise RuntimeError(f"{ENV_KEY} env var required")

    # -- preflight: the relay must actually serve the id before we touch the DB
    probe_status, probe_text, probe_ok = upstream_probe(key)
    print(f"preflight upstream probe: HTTP {probe_status} successes={probe_ok} "
          f"text={probe_text[:60]!r}")
    if probe_status != 200:
        raise RuntimeError(f"upstream relay not serving {TEST_MODEL} (HTTP {probe_status}) — aborting")

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    channels = fetch_channels(smoke, headers)
    by_name = {str(c.get("name")): c for c in channels}
    existing = by_name.get(CHANNEL_NAME)
    reused_id: int | None = None
    if existing is not None:
        mismatch = []
        if str(existing.get("base_url") or "") != BASE_URL:
            mismatch.append(f"base_url={existing.get('base_url')!r}")
        if str(existing.get("models") or "") != ",".join(MODELS):
            mismatch.append(f"models={existing.get('models')!r}")
        if int(existing.get("priority") or -1) != PRIORITY:
            mismatch.append(f"priority={existing.get('priority')!r}")
        if int(existing.get("weight") or -1) != WEIGHT:
            mismatch.append(f"weight={existing.get('weight')!r}")
        stored_raw = existing.get("model_mapping")
        stored_mapping_obj = (
            stored_raw if isinstance(stored_raw, dict) else json.loads(str(stored_raw or "") or "{}")
        )
        if stored_mapping_obj != MODEL_MAPPING:
            mismatch.append(f"model_mapping={stored_raw!r}")
        if mismatch:
            raise RuntimeError(
                f"channel {CHANNEL_NAME} (ch{existing['id']}) exists with drifted config: {mismatch}; "
                "manual reconciliation required (do not silently overwrite)"
            )
        reused_id = int(existing["id"])
        print(f"resume: reusing existing ch{reused_id} {CHANNEL_NAME} (config matches)")

    # -- routing posture guard: the declared primary must still outrank the backup
    primary_prio = db_one("SELECT priority, weight FROM abilities WHERE channel_id = ? AND model = ?",
                          (PRIMARY_CHANNEL, TEST_MODEL))
    if not primary_prio:
        print(f"  note: primary ch{PRIMARY_CHANNEL} has no ability row for {TEST_MODEL} yet")
    elif int(primary_prio[0]) <= PRIORITY:
        raise RuntimeError(
            f"primary ch{PRIMARY_CHANNEL} priority {primary_prio[0]} <= backup {PRIORITY}; "
            "backup would outrank the live carrier — refusing to enable"
        )
    else:
        print(f"  posture ok: primary ch{PRIMARY_CHANNEL} ability priority={primary_prio[0]} > {PRIORITY}")

    backup = online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    if existing is None:
        payload = {
            "name": CHANNEL_NAME,
            "type": 1,
            "base_url": BASE_URL,
            "key": key,
            "models": ",".join(MODELS),
            "model_mapping": json.dumps(MODEL_MAPPING, ensure_ascii=False),
            "group": "default",
            "priority": PRIORITY,
            "weight": WEIGHT,
            "auto_ban": 1,
            "status": 2,  # create disabled; enabled only after verification
            "test_model": TEST_MODEL,
        }
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/",
            method="POST",
            body={"mode": "single", "channel": payload},
            headers=headers,
        )
        if status != 200 or not isinstance(body, dict) or not body.get("success"):
            message = body.get("message") if isinstance(body, dict) else None
            raise RuntimeError(f"channel POST failed: HTTP {status} message={message!r}")
        after = fetch_channels(smoke, headers)
        match = [c for c in after if str(c.get("name")) == CHANNEL_NAME]
        if not match:
            raise RuntimeError(f"{CHANNEL_NAME} not visible after POST")
        new_id = int(match[0]["id"])
        print(f"created ch{new_id} {CHANNEL_NAME}")
    else:
        new_id = reused_id
        print(f"using ch{new_id} (resumed)")

    # read-back: stored model set / status must match the request exactly
    after = fetch_channels(smoke, headers)
    match = [c for c in after if int(c.get("id") or 0) == new_id]
    if not match:
        raise RuntimeError(f"ch{new_id} not visible after create")
    stored_final = set(str(match[0].get("models") or "").split(",")) - {""}
    if stored_final != set(MODELS):
        raise RuntimeError(f"ch{new_id} read-back models mismatch: delta={sorted(set(MODELS) ^ stored_final)}")
    stored_status = int(match[0].get("status") or 0)
    print(f"read-back ok: ch{new_id} models={len(stored_final)} status={stored_status}")

    # pricing: read-only parity report
    row = db_one("SELECT value FROM options WHERE key = 'ModelRatio'")
    mr = json.loads(row[0]) if row and row[0] else {}
    row = db_one("SELECT value FROM options WHERE key = 'CompletionRatio'")
    cr = json.loads(row[0]) if row and row[0] else {}
    for m in MODELS:
        if m in mr or m in cr:
            print(f"  pricing: {m} -> ModelRatio={mr.get(m)}/{cr.get(m)}")
        else:
            print(f"  pricing: {m} -> absent (gateway default, parity with ch149 precedent)")

    # -- prove the channel's upstream path while it is still DISABLED.
    # The admin channel test calls upstream through this channel's own machinery
    # (and does not require status=1), so routing is never touched by the proof.
    t_status, t_body = admin_channel_test(smoke, headers, new_id, TEST_MODEL)
    if not (isinstance(t_body, dict) and t_body.get("success")):
        message = t_body.get("message") if isinstance(t_body, dict) else None
        raise RuntimeError(f"channel test failed on ch{new_id}: HTTP {t_status} message={message!r}")

    # -- enable only now; everything below rolls the channel back on failure.
    # Enabling is routing-safe because PRIORITY sits strictly below the live
    # primary (asserted above), and abilities.enabled mirrors channel status, so
    # the abilities rebuild has to happen after the enable.
    enabled = False
    try:
        set_status(smoke, headers, new_id, 1)
        enabled = True
        print(f"enabled ch{new_id} (status=1)")

        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/fix", method="POST", body={}, headers=headers
        )
        if status != 200 or not isinstance(body, dict) or not body.get("success"):
            message = body.get("message") if isinstance(body, dict) else None
            raise RuntimeError(f"channel fix failed: HTTP {status} message={message!r}")
        for m in MODELS:
            row_ab = wait_abilities(new_id, m)
            if not row_ab or tuple(row_ab) != ("default", 1, PRIORITY, WEIGHT):
                raise RuntimeError(f"abilities ch{new_id}/{m} = {row_ab} (absent or wrong shape after 90s)")
        print(f"verify ok: abilities ch{new_id} x{len(MODELS)} (default,1,{PRIORITY},{WEIGHT})")

        gateway_key = read_gateway_key()
        primary_live = channel_status(PRIMARY_CHANNEL) == 1
        status, usage, attr = gateway_chat_retry(smoke, gateway_key, TEST_MODEL)
        if status != 200:
            raise RuntimeError(f"gateway {TEST_MODEL} chat failed: HTTP {status}")
        if attr is None:
            raise RuntimeError(f"{TEST_MODEL}: no consumption-log attribution observed")
        if primary_live:
            if attr != PRIMARY_CHANNEL:
                raise RuntimeError(
                    f"{TEST_MODEL} attributed ch{attr}, expected live primary ch{PRIMARY_CHANNEL} — "
                    "backup must not win contested traffic"
                )
            print(f"contested {TEST_MODEL}: 200 usage={usage.get('prompt_tokens')}/"
                  f"{usage.get('completion_tokens')} attributed ch{attr} (primary live, keeps traffic)")
        else:
            if attr not in (new_id, PRIMARY_CHANNEL):
                raise RuntimeError(
                    f"{TEST_MODEL}: primary ch{PRIMARY_CHANNEL} not serving but attributed ch{attr}, "
                    f"expected takeover ch{new_id}"
                )
            note = "takeover (primary down)" if attr == new_id else "primary recovered mid-request"
            print(f"contested {TEST_MODEL}: 200 attributed ch{attr} ({note})")
    except BaseException:
        if enabled:
            print(f"ROLLBACK: disabling ch{new_id} after failed verification")
            set_status(smoke, headers, new_id, 2)
        raise

    print(f"DONE: ch{new_id} {CHANNEL_NAME} live as backup tier for {MODELS}")
    print("NEXT: run scripts/ops/newapi-local-smoke.py (policy gate; zero new FAILs) "
          "+ README ledger + runbook docs/ops/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
