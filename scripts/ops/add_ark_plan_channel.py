#!/usr/bin/env python3
"""Attach the Volcengine Ark agent-plan credential as standalone -ark names.

Additive only: creates one channel, never PUTs an existing one, writes no option row.

Why Anthropic type (14) and not OpenAI type (1). This fork appends "/v1/chat/completions"
to the channel base_url, which ch205 proves (its step_plan base_url carries no /v1 and it
routes). The Ark agent plan serves OpenAI at "/api/plan/v3/chat/completions" -- there is no
"/v1" segment to land on, so no OpenAI-type base_url can produce that path. The Anthropic
base the operator supplied is "/api/plan", and "/api/plan/v1/messages" answers a real
Anthropic-shaped 200, which matches this fork's "/v1/messages" append exactly. The offer
text frames the credential as an Anthropic-protocol tool endpoint for the same reason.

Names are standalone and suffixed -ark, per the ch204 workbuddy -wb precedent, mapped to
the real upstream ids in model_mapping. They deliberately do NOT join the existing glm-5.3
or kimi-k3 pools: this is an unverified subscription relay and a client asking for glm-5.3
should not be silently served a different vendor's weights. That is the same substitution
refused for qgyz13 on 2026-10-11.

Verified before this script existed, one request per name, spaced, no retries:
glm-5.3 answered 200 with content "OK" (80 completion tokens, 78 of them reasoning),
kimi-k3 and ark-code-latest answered 200, doubao-seed-2.1-turbo answered 200 with content,
deepseek-v4.1 returned UnsupportedModel and is therefore not declared. The plan rejects
anything outside its allowlist with HTTP 404 UnsupportedModel, so the list below is the
tested set, not the vendor's full menu.

The reasoning-token behavior is the live risk. Every one of these names is a heavy
reasoning model: given max_tokens 8 or 128 they spend the whole budget thinking and return
content="" with finish/stop_reason = max_tokens. NewAPI's own management test uses a small
budget, so a healthy channel can look broken and auto_ban can disable it for a non-defect.
The probe reports the verdict verbatim; if it fails on empty content rather than transport,
the fix is a test budget, not a parking.

Pricing: these four names have no ModelRatio entry, so the first served request bills at
the fork's implied model_ratio=37.5. The -wb names were registered at 0 for the same
reason (subscription upstream, no marginal cost). This script writes no option, so that is
an operator decision, not a silent default.

Default is a dry run. --apply snapshots the DB, creates the channel disabled, probes every
declared name, waits for the channel cache, enables, then asserts the abilities row set.
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
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
KEY_PATH = Path.home() / ".new-api-local" / "ark-plan-key.txt"

CHANNEL_NAME = "ark-plan"
BASE_URL = "https://ark.cn-beijing.volces.com/api/plan"
CHANNEL_TYPE = 14  # Anthropic/Claude type; the only append shape this endpoint fits
GROUP = "default"
PRIORITY = 0  # single-leg pools: tier is moot, and p0 matches the ch204/ch205 plan channels
WEIGHT = 5
AUTO_BAN = 1
CACHE_SYNC_SECONDS = 75
PROBE_SPACING_SECONDS = 20
MODEL_MAPPING = {
    "glm-5.3-ark": "glm-5.3",
    "kimi-k3-ark": "kimi-k3",
    "doubao-seed-2.1-turbo-ark": "doubao-seed-2.1-turbo",
    "ark-code-latest-ark": "ark-code-latest",
}
MODELS = list(MODEL_MAPPING)
TEST_MODEL = "glm-5.3-ark"


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
    parser.add_argument("--token-id", type=int, default=1,
                        help="gateway token row used for the relay probe (key never printed)")
    return parser.parse_args()


def upstream_key() -> str:
    key = KEY_PATH.read_text(encoding="utf-8").strip()
    if not key.startswith("ark-") or len(key) < 20:
        raise RuntimeError(f"{KEY_PATH.name} does not look like an ark- credential")
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
        raise RuntimeError(f"multiple {CHANNEL_NAME} channels; refusing an ambiguous write")
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
        "status": 2,  # created disabled; enabled only after a name answers
        "auto_ban": AUTO_BAN,
        "model_mapping": json.dumps(MODEL_MAPPING),
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
    response_status, _ = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}/status", method="POST",
        body={"status": status}, headers=headers)
    if response_status != 200:
        raise RuntimeError(f"status POST failed: HTTP {response_status}")


def delete_channel(smoke, headers: dict[str, str], channel_id: int) -> None:
    status, _ = smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}",
                                method="DELETE", headers=headers)
    if status != 200:
        raise RuntimeError(f"channel DELETE failed: HTTP {status}")


def management_probe(smoke, headers: dict[str, str], channel_id: int, model: str) -> tuple[bool, str]:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={model}", headers=headers)
    message = ""
    if isinstance(body, dict):
        message = str(body.get("message") or "")[:140]
    return status == 200 and not (isinstance(body, dict) and body.get("success") is False), \
        f"HTTP {status} {message}".strip()


def pool_rows(db_path: Path, models: list[str]) -> list[tuple]:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        placeholders = ",".join("?" * len(models))
        return list(con.execute(
            'SELECT model, "group", channel_id, enabled, priority, weight FROM abilities '
            f"WHERE model IN ({placeholders})", list(models)))


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    destination = backup_dir / f"new-api-before-ark-plan-{time.strftime('%Y%m%d-%H%M%S')}.db"
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


def assert_standalone_names(db_path: Path) -> None:
    """These names must be new: joining an existing pool would be a routing change, not an add.

    Also discloses pricing state, because an unregistered name bills at the implied 37.5
    from its first served request. This script writes no option row.
    """
    rows = pool_rows(db_path, MODELS)
    existing = {}
    for model, group, channel_id, enabled, _prio, _w in rows:
        existing.setdefault(model, []).append(f"ch{channel_id}({group}{'/on' if enabled == 1 else '/off'})")
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        row = con.execute("SELECT value FROM options WHERE key = 'ModelRatio'").fetchone()
    try:
        ratios = json.loads(row[0]) if row and row[0] else {}
    except ValueError:
        ratios = {}
    for name in MODELS:
        clash = existing.get(name)
        status = f"CLASH already served by {clash}" if clash else "new name, no pool"
        price = f"ratio={ratios[name]}" if name in ratios else "UNREGISTERED -> implied 37.5"
        print(f"  {name:<28} {status}; {price}")
    if existing:
        raise RuntimeError(f"-ark names must be standalone, but these already exist: "
                           f"{sorted(existing)}; refusing to join an existing pool")
    unpriced = [m for m in MODELS if m not in ratios]
    if unpriced:
        print(f"  WARN: {unpriced} have no ModelRatio entry and will bill at the implied "
              "model_ratio=37.5 from their first served request. The -wb precedent registered "
              "equivalent subscription names at 0. This script does not fix that.")


def assert_row_set(pre: list[tuple], post: list[tuple], channel_id: int) -> None:
    pre_set, post_set = set(pre), set(post)
    lost = sorted(pre_set - post_set)
    added = sorted(post_set - pre_set)
    if lost:
        raise RuntimeError(f"abilities rows LOST by this write (production regression): {lost}")
    foreign = [r for r in added if r[2] != channel_id]
    if foreign:
        raise RuntimeError(f"abilities rows appeared for other channels: {foreign}")
    intended = {(m, GROUP, channel_id, 1, PRIORITY, WEIGHT) for m in MODELS}
    missing = sorted(intended - set(added))
    if missing:
        raise RuntimeError(f"expected new legs did not appear: {missing}")
    print(f"  row set ok: {len(added)} new legs all on ch{channel_id}, {len(pre)} pre rows intact")


def gateway_key(db_path: Path, token_id: int) -> str:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        row = con.execute("SELECT key FROM tokens WHERE id = ?", (token_id,)).fetchone()
    if not row or not row[0]:
        raise RuntimeError(f"token id {token_id} missing or keyless")
    return str(row[0]).strip()


def relay_probe(smoke, key: str, model: str) -> dict:
    """One non-stream request through the gateway on the client-facing -ark name.

    These pools have exactly one leg, so unlike the shared glm-5.3 pool the serving channel
    is not ambiguous here: this proves the -ark name routes AND that ch<id> answers.
    max_tokens is deliberately generous because every declared name is a heavy reasoning
    model that spends a small budget entirely on thinking and returns empty content.
    """
    body = {"model": model, "max_tokens": 512,
            "messages": [{"role": "user", "content": "Reply with exactly OK"}], "stream": False}
    request = urllib.request.Request(
        f"{smoke.NEWAPI_BASE}/v1/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(request, timeout=170) as response:
            parsed = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"relay {model}: HTTP {error.code} "
                           f"{error.read().decode('utf-8', 'replace')[:200]}") from error
    except Exception as error:
        raise RuntimeError(f"relay {model}: {type(error).__name__}: {error}") from error
    choice = (parsed.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    return {"model": model, "content": message.get("content"),
            "thinking_chars": len(str(message.get("reasoning_content") or "")),
            "finish": choice.get("finish_reason"), "usage": parsed.get("usage"),
            "sec": round(time.time() - t0, 2)}


def log_rows(db_path: Path, channel_id: int, since: int) -> list[dict]:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id, model_name, type, quota, prompt_tokens, completion_tokens, created_at "
            "FROM logs WHERE channel_id = ? AND created_at >= ? ORDER BY id DESC LIMIT 20",
            (channel_id, since)).fetchall()
    return [dict(r) for r in rows]


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
        return 0

    if existing and (existing.get("base_url") != BASE_URL or existing.get("type") != CHANNEL_TYPE
                     or existing.get("group") != GROUP):
        raise RuntimeError("existing ark-plan channel no longer matches the inspected contract")

    plan = f"use existing ch{channel_id}" if channel_id else f"create {CHANNEL_NAME} -> ch<new>"
    print(f"plan: {plan}; models {MODELS}; mapping {MODEL_MAPPING}; group {GROUP}; "
          f"priority {PRIORITY} weight {WEIGHT} auto_ban {AUTO_BAN}; type {CHANNEL_TYPE} "
          f"(Anthropic append); base {BASE_URL}; key {redact(key)} (value never printed)")
    print("standalone-name premise")
    assert_standalone_names(db_path)

    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")
    pre = pool_rows(db_path, MODELS)
    started = int(time.time())
    try:
        if channel_id is None:
            channel_id = create_channel(smoke, headers, key)
            print(f"created ch{channel_id} disabled")
        print(f"management probes, one request per name, {PROBE_SPACING_SECONDS}s apart")
        results = {}
        for i, model in enumerate(MODELS):
            if i:
                time.sleep(PROBE_SPACING_SECONDS)
            ok, detail = management_probe(smoke, headers, channel_id, model)
            results[model] = (ok, detail)
            print(f"  {model:<28} {'ok' if ok else 'FAIL'} {detail}")
        routed = sorted(m for m, (ok, _) in results.items() if ok)
        if not routed:
            raise RuntimeError(f"no name answered on ch{channel_id}: "
                               f"{ {m: d for m, (_, d) in results.items()} }")
        dead = sorted(set(MODELS) - set(routed))
        if dead:
            print(f"  WARN: {dead} did not answer; they stay declared, and the rows above are "
                  "the evidence that they are not routable today. Empty content with "
                  "stop=max_tokens on these reasoning models is a test-budget artifact, not "
                  "necessarily an upstream defect.")

        print(f"waiting {CACHE_SYNC_SECONDS}s for the channel cache", flush=True)
        time.sleep(CACHE_SYNC_SECONDS)
        set_status(smoke, headers, channel_id, 1)
        print(f"ch{channel_id} enabled; waiting {CACHE_SYNC_SECONDS}s for abilities to route", flush=True)
        time.sleep(CACHE_SYNC_SECONDS)

        post = pool_rows(db_path, MODELS)
        print("abilities regression guard")
        assert_row_set(pre, post, channel_id)

        gkey = gateway_key(db_path, args.token_id)
        print(f"relay probe via token{args.token_id} sha12="
              f"{hashlib.sha256(gkey.encode()).hexdigest()[:12]}")
        for model in routed[:2]:
            r = relay_probe(smoke, gkey, model)
            print(f"  relay {r['model']}: content={str(r['content'])[:40]!r} "
                  f"thinking_chars={r['thinking_chars']} finish={r['finish']} usage={r['usage']} {r['sec']}s")
            time.sleep(5)

        rows = log_rows(db_path, channel_id, started)
        served = {r["model_name"] for r in rows if r["type"] == 2 and r["completion_tokens"] > 0}
        empty = [r["id"] for r in rows if r["type"] == 2 and r["completion_tokens"] == 0]
        print(f"log attribution on ch{channel_id}: {len(rows)} rows, models with output "
              f"{sorted(served) or 'none'}, zero-output rows {empty or 'none'}")
        print(f"OK: ch{channel_id} live. Snapshot: {backup.name}")
        print("NOTE: relay probes prove these -ark names route end to end on this single-leg "
              "channel. They do not add these names to any existing pool, and they do not "
              "register pricing -- see the ModelRatio warning above.")
        return 0
    except Exception as error:
        if channel_id is not None:
            try:
                set_status(smoke, headers, channel_id, 2)
                print(f"ch{channel_id} disabled as the fail-closed step")
            except Exception as disable_error:
                print(f"WARN: could not disable ch{channel_id}: {disable_error}")
        print(f"registration failed: {str(error)[:400]}\nRecovery snapshot: {backup.name}")
        raise


if __name__ == "__main__":
    sys.exit(main())
