#!/usr/bin/env python3
"""Attach the ciallo.bar relay as a deepseek-v4.1-flash fallback leg.

Additive only, per the standing ruling for imported credentials: creates one channel,
never PUTs an existing one, writes no option row.

The offer's own terms: registration is closed and must not be attempted (not attempted --
the credential arrived already issued); chat/response/anthropic are all served; $20 of quota
spendable ONLY on deepseek-v4.1-flash, about 1B tokens; concurrency capped at 15; the box is
shared and weak ("蹬轻点"); 200-300 tps; another aggregator, not a model owner.

Read-only inspection before this script existed, one request per check: GET /v1/models returns
exactly one id, ["deepseek-v4.1-flash"], so declaring anything else would be inventing
capacity. A chat completion answered content "OK" with finish=stop in 2.38s and a small budget
is enough (completion_tokens=2 against max_tokens=16) -- this name is not a heavy reasoner,
unlike the -ark set attached the same day.

The upstream is load-shedding in bursts and that is normal for it, not a defect. Measured on
2026-10-11: the first enable attempt's management probe returned "503 system cpu overloaded
(current: 97.2%, threshold: 90%)" -- ciallo.bar runs its own NewAPI with a CPU guard -- and the
same probe three minutes later answered in 2.19s. So a single spaced retry is the correct
response to that message; treating it as a dead credential, or parking the channel on it, is
not. Two of them happened back to back, which is also why the probe budget stays at one request
per stage.

THE EDGE IS THE PART THAT WILL WASTE YOUR TIME. ciallo.bar sits behind a Cloudflare
browser-signature rule: with this script family's default urllib User-Agent, /v1/models,
/v1/chat/completions and /v1/messages ALL answer "HTTP 403 :: error code: 1010" without ever
reaching the origin. Explicitly sending Go-http-client/1.1 -- what NewAPI's own relay sends --
or an empty UA answers 200. So the channel needs no header_override and works as shipped, but
a hand probe from this machine will read as "credential dead" when the truth is "your UA was
refused". Set a browser-ish or Go UA when probing this host by hand; and note that 1010 is not
an authentication failure, so it must never be treated as evidence the key was revoked.

p20/w1 puts it strictly below the three enabled legs (ch203 p30 w3, ch208 p30 w1, ch15 p25 w1),
so it serves only when a higher tier cannot. w1 also keeps a single gateway request from
turning into a burst against a 15-concurrency upstream. The pool premise is asserted rather
than assumed: if deepseek-v4.1-flash had no enabled leg, adding one would advertise capacity
that does not exist.

Pricing is disclosed, not fixed: deepseek-v4.1-flash is NOT in ModelRatio, so it already bills
at the fork's implied model_ratio=37.5 today on the existing legs -- this attach adds no new
name and no new exposure, it only shares an existing one. (The -wb variant of the same model is
registered at 0.) Writing an option row is outside the standing rule for imported credentials.

base_url carries no /v1 suffix because the fork appends the path, and hosts with an SPA
catch-all answer 200 with HTML and an empty stream when the suffix is doubled.

Probe budget is one management probe plus one relay probe, no retries. Burst short-prompt
probing got the yanglechu key (ch206) banned upstream, and a shared $20 quota on a weak box is
spent the same way.

Default is a dry run. --apply snapshots the DB, creates the channel disabled, probes
deepseek-v4.1-flash, waits for the channel cache, enables, then asserts the abilities row set.
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
KEY_PATH = Path.home() / ".new-api-local" / "ciallo-key.txt"

CHANNEL_NAME = "ciallo"
BASE_URL = "https://ciallo.bar"
CHANNEL_TYPE = 1  # OpenAI-compatible; /v1/chat/completions answered directly
GROUP = "default"
PRIORITY = 20  # strictly below ch203/ch208 at p30 and ch15 at p25: fallback tier, not a co-primary
WEIGHT = 1     # a heavier draw risks the upstream's concurrency 15 cap on a shared weak box
AUTO_BAN = 1
CACHE_SYNC_SECONDS = 75
MODELS = ["deepseek-v4.1-flash"]  # the only id /v1/models lists under this credential
TEST_MODEL = "deepseek-v4.1-flash"


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
    if not key.startswith("sk-") or len(key) < 20:
        raise RuntimeError(f"{KEY_PATH.name} does not look like an sk- credential")
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
        "status": 2,  # created disabled; enabled only after the name answers
        "auto_ban": AUTO_BAN,
        # The upstream id is served verbatim, so no mapping is needed -- and no header_override
        # either: NewAPI's Go client passes the Cloudflare signature rule (see module docstring).
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
    """(model, group, channel_id, enabled, priority, weight) for every leg of these pools."""
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        placeholders = ",".join("?" * len(models))
        return list(con.execute(
            'SELECT model, "group", channel_id, enabled, priority, weight FROM abilities '
            f"WHERE model IN ({placeholders})", list(models)))


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    destination = backup_dir / f"new-api-before-ciallo-{time.strftime('%Y%m%d-%H%M%S')}.db"
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


def report_pricing_exposure(db_path: Path) -> None:
    """Unregistered names bill at the fork's implied ratio from their first request.

    This script writes no option, and pricing is the operator's call, so the exposure is
    disclosed rather than fixed or refused: this pool already exists and already bills this way.
    """
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        row = con.execute("SELECT value FROM options WHERE key = 'ModelRatio'").fetchone()
        try:
            ratios = json.loads(row[0]) if row and row[0] else {}
        except ValueError:
            print("  WARN: ModelRatio is unparsable; pricing state unknown")
            return
    priced = {m: ratios[m] for m in MODELS if m in ratios}
    unpriced = [m for m in MODELS if m not in ratios]
    print(f"  ModelRatio registered: {priced or 'none'}")
    if unpriced:
        print(f"  NOTE: {unpriced} has no ModelRatio entry and bills at the implied "
              "model_ratio=37.5 -- it already does on the existing legs, so this attach shares an "
              "exposure rather than creating one. This script does not fix that.")


def assert_prerequisites(db_path: Path) -> None:
    for name in MODELS:
        legs = [r for r in pool_rows(db_path, [name]) if r[1] == GROUP]
        live = [r for r in legs if r[3] == 1]
        if not legs:
            raise RuntimeError(f"{name} has no group-default pool; the shared-pool premise is false")
        if not live:
            raise RuntimeError(f"{name} pool has no enabled leg; adding one more leg to a dead "
                               "pool would advertise capacity that does not exist")
        higher = [r for r in live if r[4] > PRIORITY]
        if not higher:
            raise RuntimeError(f"p{PRIORITY} is not a fallback tier for {name}: no enabled leg "
                               f"sits above it, so this channel would become primary traffic")
        print(f"  {name:<22} pool legs={len(legs)} enabled={len(live)} "
              f"live_ids={[r[2] for r in live]} prio={[r[4] for r in live]}")
    report_pricing_exposure(db_path)


def assert_row_set(pre: list[tuple], post: list[tuple], channel_id: int) -> None:
    """Every leg that is not this channel's own must survive untouched.

    This channel's own row flipping enabled 0 -> 1 is the intended write, so it is excluded
    from the loss test: re-running this script against a channel an earlier attempt left
    disabled carries that disabled row in `pre`, and the fork rebuilds the row on enable.
    Reading that rebuild as a lost row aborted a successful attach on 2026-10-11 and then
    fail-closed the channel back off -- a guard that destroys the state it just proved is
    worse than no guard. The intended row must still appear, so a rebuild that produced the
    wrong tier is still caught below.
    """
    pre_set, post_set = set(pre), set(post)
    lost = sorted(r for r in (pre_set - post_set) if r[2] != channel_id)
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
    own_flip = sorted({r for r in pre_set if r[2] == channel_id} - {r for r in post_set if r[2] == channel_id})
    print(f"  row set ok: {len(added)} new legs all on ch{channel_id}, {len(pre)} pre rows intact"
          + (f", own pre-disable rows rebuilt: {own_flip}" if own_flip else ""))


def gateway_key(db_path: Path, token_id: int) -> str:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        row = con.execute("SELECT key FROM tokens WHERE id = ?", (token_id,)).fetchone()
    if not row or not row[0]:
        raise RuntimeError(f"token id {token_id} missing or keyless")
    return str(row[0]).strip()


def relay_probe(smoke, key: str, model: str) -> dict:
    """One non-stream request through the gateway, proving the name routes at all.

    Which leg answers is not controllable here (priority tiers and weighted random sit in
    front of this channel), so the attributed channel is read back from the logs instead of
    assumed. Small budget is deliberate: this name answered finish=stop at 2 completion tokens.
    """
    body = {"model": model, "max_tokens": 64,
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
    return {"model": model, "content": (choice.get("message") or {}).get("content"),
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
        raise RuntimeError("existing ciallo channel no longer matches the inspected contract")

    plan = f"use existing ch{channel_id}" if channel_id else f"create {CHANNEL_NAME} -> ch<new>"
    print(f"plan: {plan}; models {MODELS}; group {GROUP}; priority {PRIORITY} weight {WEIGHT} "
          f"auto_ban {AUTO_BAN}; type {CHANNEL_TYPE}; model_mapping empty; base {BASE_URL}; "
          f"key {redact(key)} (value never printed)")
    print("pool premise")
    assert_prerequisites(db_path)

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
        ok, detail = management_probe(smoke, headers, channel_id, TEST_MODEL)
        print(f"  {TEST_MODEL:<22} {'ok' if ok else 'FAIL'} {detail}")
        if not ok:
            raise RuntimeError(f"{TEST_MODEL} did not answer on ch{channel_id}: {detail}. "
                               "If the message carries a Cloudflare 403/1010 shape the edge is "
                               "refusing the client, not the credential -- do not conclude the "
                               "key was revoked from that alone.")

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
        r = relay_probe(smoke, gkey, TEST_MODEL)
        print(f"  relay {r['model']}: content={r['content']!r} finish={r['finish']} "
              f"usage={r['usage']} {r['sec']}s")

        rows = log_rows(db_path, channel_id, started)
        served = {x["model_name"] for x in rows if x["type"] == 2 and x["completion_tokens"] > 0}
        empty = [x["id"] for x in rows if x["type"] == 2 and x["completion_tokens"] == 0]
        print(f"log attribution on ch{channel_id}: {len(rows)} rows, models with output "
              f"{sorted(served) or 'none'}, zero-output rows {empty or 'none'}")
        print(f"OK: ch{channel_id} live at priority {PRIORITY}. Snapshot: {backup.name}")
        print("NOTE: the relay probe proves the gateway answered deepseek-v4.1-flash end to end. "
              "It does not prove which leg served, and it does not verify the relay is not "
              "serving a different model underneath; p20/w1 keeps this leg in fallback while "
              "that is unknown.")
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
