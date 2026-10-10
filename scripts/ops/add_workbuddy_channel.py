#!/usr/bin/env python3
"""Aggregate WorkBuddy's built-in models into the local NewAPI.

Upstream is the codebuddy2openai converter on 127.0.0.1:18801, which holds the
WorkBuddy desktop bearer in RAM only (captured with WB_RESIDENT=1; no credential
is ever written here). The channel key is a fixed non-secret placeholder because
the converter ignores it.

Names are prefixed (hy3-wb, hy4-wb, deepseek-v4.1-flash-wb) on purpose: plain
deepseek-v4.1-flash is a live pool shared by ch15+ch203 and plain hy3 belongs to
ch111, so unprefixed names would silently pull WorkBuddy into existing routing.

hy4-wb maps to hy4-preview-f, the credits x0.00 build of "Hy4 preview" in the
desktop account catalog (plain hy4-preview is that same model at x0.29, hy3 is
also x0.00, deepseek-v4.1-flash costs x0.11). Ids and limits come from that
cached catalog; only a live completion proves this account may call them.

ModelRatio/CompletionRatio are pinned to 0 for the new names only, never for a
model another channel already bills. WorkBuddy plan credits are the real cost
here, so a second local charge would be double-billing a loopback bridge.

Default is a dry run. --apply snapshots the DB, creates the channel disabled,
requires a management probe on every model, waits for the fork's channel cache,
enables, then relays non-stream and stream through the gateway and checks that
the request rows attribute to the new channel. --teardown reverses it.

Growing an existing channel's model list goes through direct SQL, not PUT: this
fork round-trips key:"" from GET on a channel PUT and would blank the stored key.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")

CHANNEL_NAME = "workbuddy-local-bridge"
BASE_URL = "http://127.0.0.1:18801"  # local bridge; NewAPI appends /v1/chat/completions
CONVERTER_HEALTH = f"{BASE_URL}/health"
CHANNEL_KEY = "local-loopback-no-upstream-key"
GROUP = "default"
PRIORITY = 0
WEIGHT = 5
AUTO_BAN = 0  # RAM-only bearer: an auth failure must not queue a disable
CACHE_SYNC_SECONDS = 75
MODEL_MAP = {
    "hy3-wb": "hy3",
    "hy4-wb": "hy4-preview-f",
    "deepseek-v4.1-flash-wb": "deepseek-v4.1-flash",
}
TEST_MODEL = "hy3-wb"


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
                        help="delete the channel and drop only the ratio keys this script added")
    parser.add_argument("--token-id", type=int, default=1,
                        help="gateway token row used for the relay probe (key never printed)")
    return parser.parse_args()


def converter_health() -> dict:
    try:
        with urllib.request.urlopen(CONVERTER_HEALTH, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as error:
        raise RuntimeError(f"converter not reachable at {BASE_URL}: {error}") from error


def list_channels(smoke, headers: dict[str, str]) -> list[dict]:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=200", headers=headers
    )
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


def channel_payload() -> dict:
    return {
        "name": CHANNEL_NAME,
        "type": 1,  # OpenAI
        "key": CHANNEL_KEY,
        "base_url": BASE_URL,
        "models": ",".join(MODEL_MAP),
        "group": GROUP,
        "test_model": TEST_MODEL,
        "priority": PRIORITY,
        "weight": WEIGHT,
        "status": 2,  # created disabled; enabled only after probes
        "auto_ban": AUTO_BAN,
        "model_mapping": json.dumps(MODEL_MAP, separators=(",", ":")),
    }


def create_channel(smoke, headers: dict[str, str]) -> int:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/", method="POST",
        body={"mode": "multi_to_single", "channel": channel_payload()}, headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"channel POST failed: HTTP {status} {str(body)[:200]}")
    created = find_channel(smoke, headers)
    if not created or not isinstance(created.get("id"), int):
        raise RuntimeError(f"{CHANNEL_NAME} missing/ambiguous on readback")
    return int(created["id"])


def update_channel(smoke, headers: dict[str, str], db_path: Path, channel_id: int) -> None:
    """Grow the model list of an existing channel, staying disabled.

    No PUT here. This fork's channel PUT round-trips key:"" back out of GET and
    wipes the stored key, and rejects a body carrying status with 'Invalid
    parameters' (see adjust_deepseek_v4_flash_pool.py and the set_status
    contract). The precedent is a direct UPDATE plus the ~60s channel cache.
    Abilities get one new row per model, enabled=0, so nothing routes until the
    status POST flips them.
    """
    models = ",".join(MODEL_MAP)
    mapping = json.dumps(MODEL_MAP, separators=(",", ":"))
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        with conn:
            conn.execute("UPDATE channels SET models = ?, model_mapping = ? WHERE id = ?",
                         (models, mapping, channel_id))
            have = {row[0] for row in conn.execute(
                "SELECT model FROM abilities WHERE channel_id = ?", (channel_id,))}
            for model in MODEL_MAP:
                if model in have:
                    continue
                conn.execute(
                    'INSERT INTO abilities ("group", model, channel_id, enabled, priority,'
                    " weight, tag) VALUES (?, ?, ?, 0, ?, ?, NULL)",
                    (GROUP, model, channel_id, PRIORITY, WEIGHT))
        row = conn.execute("SELECT models, model_mapping, status FROM channels WHERE id = ?",
                           (channel_id,)).fetchone()
        if not row or row[0] != models or row[1] != mapping:
            raise RuntimeError(f"ch{channel_id} SQL readback wrong: {row!r}")
        if int(row[2]) != 2:
            raise RuntimeError(f"ch{channel_id} must still be disabled before probes, got status={row[2]}")
        count = conn.execute("SELECT COUNT(*) FROM abilities WHERE channel_id = ? AND enabled = 0",
                             (channel_id,)).fetchone()[0]
    if count < len(MODEL_MAP):
        raise RuntimeError(f"ch{channel_id} has only {count} ability rows for {len(MODEL_MAP)} models")

    # The server must see the new list too, or the management probe 404s the model.
    seen = find_channel(smoke, headers)
    if not seen or set((seen.get("models") or "").split(",")) != set(MODEL_MAP):
        raise RuntimeError(f"API readback for ch{channel_id} still shows "
                           f"{(seen or {}).get('models')!r}; cache has not picked up the UPDATE")


def set_status(smoke, headers: dict[str, str], channel_id: int, status: int) -> None:
    response_status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}/status", method="POST",
        body={"status": status}, headers=headers,
    )
    if response_status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"status POST failed: HTTP {response_status}")


def delete_channel(smoke, headers: dict[str, str], channel_id: int) -> None:
    response_status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}", method="DELETE", headers=headers,
    )
    if response_status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"channel DELETE failed: HTTP {response_status}")


def management_probe(smoke, headers: dict[str, str], channel_id: int, model: str) -> None:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={model}",
        headers=headers, timeout=170,
    )
    text = json.dumps(body) if isinstance(body, (dict, list)) else str(body)
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"management probe failed for {model}: HTTP {status} {text[:200]}")
    print(f"  management {model}: ok time={body.get('time')}")


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"new-api-before-workbuddy-{time.strftime('%Y%m%d-%H%M%S')}.db"
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


def get_option_db(db_path: Path, key: str) -> str:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        row = con.execute("SELECT value FROM options WHERE key = ?", (key,)).fetchone()
    return row[0] if row and row[0] else "{}"


def put_option(smoke, headers: dict[str, str], key: str, value: str) -> None:
    status, _ = smoke.http_json(f"{smoke.NEWAPI_BASE}/api/option/", method="PUT",
                                body={"key": key, "value": value}, headers=headers)
    if status != 200:
        raise RuntimeError(f"option {key!r} update failed: HTTP {status}")


def pin_ratios(smoke, headers: dict[str, str], db_path: Path) -> list[str]:
    """Add a 0 ratio for missing names only. Existing keys are never rewritten."""
    touched = []
    for option in ("ModelRatio", "CompletionRatio"):
        current = get_option_db(db_path, option)
        try:
            table = json.loads(current)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{option} is invalid JSON") from error
        if not isinstance(table, dict):
            raise RuntimeError(f"{option} must be a JSON object")
        missing = [m for m in MODEL_MAP if m not in table]
        if not missing:
            print(f"  {option}: both names already present, untouched")
            continue
        for model in missing:
            table[model] = 0
        put_option(smoke, headers, option, json.dumps(table, separators=(",", ":"), sort_keys=True))
        touched.append(option)
        print(f"  {option}: pinned {missing} to 0")
    return touched


def unpin_ratios(smoke, headers: dict[str, str], db_path: Path) -> None:
    for option in ("ModelRatio", "CompletionRatio"):
        table = json.loads(get_option_db(db_path, option))
        if not any(m in table for m in MODEL_MAP):
            continue
        for model in MODEL_MAP:
            table.pop(model, None)
        put_option(smoke, headers, option, json.dumps(table, separators=(",", ":"), sort_keys=True))
        print(f"  {option}: dropped the bridge model keys")


def gateway_key(db_path: Path, token_id: int) -> tuple[str, str]:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        row = con.execute("SELECT key, \"group\" FROM tokens WHERE id = ?", (token_id,)).fetchone()
    if not row or not row[0]:
        raise RuntimeError(f"token id {token_id} missing or keyless")
    key = str(row[0]).strip()
    redacted = f"token{token_id} len={len(key)} sha12={hashlib.sha256(key.encode()).hexdigest()[:12]}"
    return key, redacted


def relay_probe(smoke, key: str, model: str, stream: bool) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": "You are a helpful assistant."},
                     {"role": "user", "content": "Reply with exactly OK"}],
        "max_tokens": 64,
        "stream": stream,
    }
    request = urllib.request.Request(
        f"{smoke.NEWAPI_BASE}/v1/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(request, timeout=170) as response:
            raw, status = response.read().decode("utf-8", "replace"), response.status
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"relay {model} stream={stream}: HTTP {error.code} "
                           f"{error.read().decode('utf-8','replace')[:200]}") from error
    except Exception as error:
        raise RuntimeError(f"relay {model} stream={stream}: {error}") from error
    if stream:
        data = [l for l in raw.splitlines() if l.startswith("data:")]
        result = {"model": model, "stream": True, "http": status, "chunks": len(data),
                  "error_in_stream": '"error"' in raw, "sec": round(time.time() - t0, 2)}
        if not data or result["error_in_stream"] or not raw.rstrip().endswith("[DONE]"):
            raise RuntimeError(f"relay stream {model} produced no clean SSE: {raw[:200]}")
        return result
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"relay {model} returned non-JSON: {raw[:200]}") from error
    choices = parsed.get("choices") or []
    content = ((choices[0].get("message") or {}).get("content") if choices else None) or ""
    result = {"model": model, "stream": False, "http": status,
              "content": content[:40] or None,
              "finish": (choices[0].get("finish_reason") if choices else None),
              "usage": parsed.get("usage"), "sec": round(time.time() - t0, 2)}
    if status != 200 or not content.strip():
        raise RuntimeError(f"relay {model} empty/failed: {json.dumps(result)[:300]}")
    return result


def log_rows(db_path: Path, channel_id: int, models: list[str]) -> list[dict]:
    since = int(time.time()) - 900
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id, model_name, channel_id, quota, prompt_tokens, completion_tokens, is_stream, use_time "
            "FROM logs WHERE channel_id = ? AND model_name IN ({}) AND created_at >= ? "
            "ORDER BY id DESC LIMIT 8".format(",".join("?" * len(models))),
            (channel_id, *models, since),
        ).fetchall()
    return [dict(r) for r in rows]


def verify_abilities(db_path: Path, channel_id: int) -> None:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        for model in MODEL_MAP:
            row = con.execute("SELECT enabled, \"group\" FROM abilities WHERE channel_id = ? AND model = ?",
                              (channel_id, model)).fetchone()
            if row is None:
                raise RuntimeError(f"abilities row missing for {model}")
            if row[0] != 1:
                raise RuntimeError(f"abilities row for {model} not enabled")
    print(f"  abilities: {len(MODEL_MAP)} rows enabled for ch{channel_id}")


def main() -> int:
    args = parse_args()
    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    existing = find_channel(smoke, headers)
    channel_id = int(existing["id"]) if existing else None
    if existing and (existing.get("base_url") != BASE_URL or existing.get("type") != 1
                     or existing.get("group") != GROUP):
        raise RuntimeError("existing channel no longer matches the inspected contract")
    grows = bool(existing) and set((existing.get("models") or "").split(",")) != set(MODEL_MAP)

    if args.teardown:
        if channel_id is None:
            print("no workbuddy channel to tear down")
            return 0
        if not args.apply:
            print(f"dry-run: would DELETE ch{channel_id} and unpin {sorted(MODEL_MAP)} from ratios")
            return 0
        backup = online_backup(db_path)
        print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")
        delete_channel(smoke, headers, channel_id)
        unpin_ratios(smoke, headers, db_path)
        print(f"OK: ch{channel_id} deleted. Recovery snapshot: {backup.name}")
        return 0

    # The bearer is RAM-only, so teardown and dry-run must work while the
    # converter is down; only a live cutover needs an authenticated session.
    health = None
    try:
        health = converter_health()
        print(f"converter: status={health.get('status')} logged_in={health.get('logged_in')} "
              f"token_expired={health.get('token_expired')}")
    except RuntimeError as error:
        print(f"converter: {error}")

    plan = f"update ch{channel_id}" if grows else (f"set ch{channel_id}" if channel_id
                                                   else f"create {CHANNEL_NAME}")
    print(f"plan: {plan}; models {MODEL_MAP}; group {GROUP}; key is a non-secret placeholder")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    if not health or health.get("status") != "ok" or not health.get("logged_in"):
        raise RuntimeError("converter is not serving an authenticated session; re-capture first")

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")
    try:
        if channel_id is None:
            channel_id = create_channel(smoke, headers)
            print(f"created ch{channel_id} disabled")
        elif grows:
            update_channel(smoke, headers, db_path, channel_id)
            print(f"updated ch{channel_id} to {sorted(MODEL_MAP)}, still disabled")
        # Ratios before probes: a management probe bills against whatever ratio
        # table is live when it runs, so probing first charged the gateway token
        # ~113k quota for the three probe rows on every --apply.
        pin_ratios(smoke, headers, db_path)
        for model in MODEL_MAP:
            management_probe(smoke, headers, channel_id, model)
        print(f"waiting {CACHE_SYNC_SECONDS}s for the channel cache", flush=True)
        time.sleep(CACHE_SYNC_SECONDS)
        set_status(smoke, headers, channel_id, 1)
        print(f"ch{channel_id} enabled; waiting {CACHE_SYNC_SECONDS}s for abilities to route", flush=True)
        time.sleep(CACHE_SYNC_SECONDS)
        verify_abilities(db_path, channel_id)
        key, redacted = gateway_key(db_path, args.token_id)
        print(f"relay probe via {redacted}")
        results = []
        for model in MODEL_MAP:
            results.append(relay_probe(smoke, key, model, False))
            results.append(relay_probe(smoke, key, model, True))
            time.sleep(1)
        for r in results:
            print(f"  relay {r['model']} stream={r['stream']}: HTTP {r['http']} "
                  f"content={r.get('content') or r.get('chunks')} finish={r.get('finish')} "
                  f"usage={r.get('usage')}")
        time.sleep(3)
        rows = log_rows(db_path, channel_id, list(MODEL_MAP))
        if len(rows) < len(results):
            raise RuntimeError(f"only {len(rows)} of {len(results)} relay rows attributed to ch{channel_id}")
        zero_output = [r["id"] for r in rows if r["completion_tokens"] == 0]
        print(f"log attribution: {len(rows)} rows on ch{channel_id}, "
              f"zero-output rows {zero_output or 'none'}, quota sum {sum(r['quota'] for r in rows)}")
        print(f"OK: ch{channel_id} aggregates WorkBuddy through the gateway")
        return 0
    except Exception as error:
        if channel_id is not None:
            try:
                set_status(smoke, headers, channel_id, 2)
                print(f"ch{channel_id} disabled as the fail-closed step")
            except Exception as disable_error:
                print(f"WARN: could not disable ch{channel_id}: {disable_error}")
        print(f"cutover failed: {str(error)[:400]}\nRecovery snapshot: {backup.name}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
