#!/usr/bin/env python3
"""Attach the metapi.lilililwan.xyz relay as three channels over three keys.

Additive only: creates three channels, never PUTs an existing one, writes no option row.

THE TERM THAT SHOULD SHAPE HOW THIS IS USED. The offer states, three times, that the upstream
may train on the traffic it is given, and that it must not carry real business or anything with
disclosable content. That is not a performance caveat, it is a consent term, and it is the
operator's call -- which is why this attach went in only after the routing shape was put to them
and answered: deepseek-v4.1-flash joins the shared default pool at p20/w1 anyway. From the
moment this channel is enabled, every prompt that pool serves can land on an upstream that says
it may train on it. Reverting is --teardown, and the snapshot named at the end of --apply is the
rollback artifact.

The offer also gives this attach an expiry: $500 per key, gone when spent or closed automatically
at 09:00 the next morning (this was written on 2026-10-11, so roughly 14 hours of life). Nothing
here schedules anything about that. After expiry the legs will start failing and auto_ban will
park them; the correct end state is a teardown, not a revival, because there is no way to top
this up.

Three channels, not one channel with three keys, because the keys are independent $500 buckets
and the measured behavior is per-key, not per-host. Read-only inspection before this script
existed, one request per check: GET /v1/models returns exactly
["deepseek-v4.1-flash", "claude-haiku-5.5", "mimo-v2.6-flash"] on ALL THREE keys, so all three
authenticate and none is narrower than the others. Chat probes on key1 answered claude-haiku-5.5
in 2.19s and deepseek-v4.1-flash in 2.45s, both content "OK" finish=stop, and answered
mimo-v2.6-flash with HTTP 429 and an EMPTY body; key3 drew the same 429 on the same name; key2
answered that name in 5.18s. So the 429 is a per-key rate limit on a shared box, not a dead
model, and --because NewAPI's auto_ban treats 429 as retryable-- splitting the keys keeps one
throttled bucket from looking like a broken capability. It also means a probe verdict of "429"
here must be recorded as inconclusive, never as "this name does not work".

None of these names is a heavy reasoner: all three answered finish=stop with real content inside
a 64-token budget, so no per-name test-budget workaround is needed (contrast the -ark attach the
same day).

EDGE NOTE, same family as ciallo.bar. This host sits behind a Cloudflare browser-signature rule:
with urllib's default User-Agent, /v1/models and /v1/chat/completions both answer
"HTTP 403 :: error code: 1010" without reaching the origin. Sending Go-http-client/1.1 -- which
is exactly what NewAPI's own relay sends -- answers 200, as do curl and the openai-python UA. So
the channel works as shipped and needs no header_override, but a hand probe from this machine
reads as "credential dead" when the truth is "your UA was refused". 1010 is not an authentication
failure and is never evidence a key was revoked.

Pools are two different premises and each is asserted, not assumed:
  - deepseek-v4.1-flash already has a live shared pool. Adding a leg here is a routing change and
    needs a higher enabled tier to sit above it, otherwise p20 would quietly become primary. At
    attach time the enabled legs were ch203 p30 w3, ch208 p30 w1, ch15 p25 w1; ch211 (ciallo, the
    p20 w1 leg attached earlier the same evening) went status=2 at 19:30:40 when its upstream
    answered the very next test with HTTP 401 `无效的令牌` -- Guardian pulled it, not this write. So
    the tier premise above p20 holds and the pool's own fallback depth is one leg shallower than
    the numbers in this paragraph suggest. Read the guard's INDEPENDENT OUTAGE line rather than
    trusting any static list of legs, because these change faster than a docstring does.
  - claude-haiku-5.5 and mimo-v2.6-flash have NO abilities row at all, so declaring their real
    names creates a brand-new single-leg pool instead of substituting weights under a name some
    other vendor already answers. They are carried under their own ids with no model_mapping, so
    no existing consumer can reach them until someone wires them up on purpose.

THE GUARD ATTRIBUTES INSTEAD OF ASSUMING, and that is the load-bearing part of this script. An
earlier version compared whole abilities rows including `enabled` and aborted if any foreign row
disappeared. `enabled` is DERIVED state -- the fork writes it from the channel's own status -- so
when ch211 was auto-banned mid-window the guard blamed this write, then disabled ch212/213/214 to
"fail closed" over an outage it had not caused, destroying the attach it had just proven. The
current guard splits it: a foreign TIER vanishing or appearing is this write's fault and aborts,
while a foreign row merely going disabled is cross-read against that channel's `status` and
reported as an independent outage if the channel itself is down. Only status=1-with-a-disabled-leg
-- the genuine clobber signature -- still aborts.

THE SAME ATTRIBUTION RULE APPLIES ONE STAGE LATER, at the relay probe, and it took a second failed
run to see it. The relay goes through the gateway, so on deepseek-v4.1-flash it is served by
whichever enabled leg wins the draw -- always ch203/ch208/ch15, all above this p20 tier. When that
pool answered HTTP 429 `You have reached your weekly Clinepass limit` (ch203, task #24) the script
read it as a metapi failure and fail-closed three healthy channels again. So the pooled name can
now only ever WARN: a shared-pool relay result is not evidence about this write. A name whose sole
legs are these channels is different -- there, `分组 default 下模型 X 无可用渠道` or a hard failure
after one spaced retry is this write's and still aborts. Retries are bounded and never spent on the
shared name, because re-probing it hammers someone else's weekly cap.

Pricing is disclosed, not fixed. All three names, and deepseek-v4.1-flash itself, are absent from
ModelRatio, so they bill at the fork's implied model_ratio=37.5. The two new names acquire that
exposure the first time anything serves them. Writing an option row is outside the standing rule
for imported credentials, so it stays an operator decision.

base_url carries no trailing slash and no /v1 because the fork appends the path, and hosts with an
SPA catch-all answer 200 with HTML and an empty stream when the suffix is doubled.

Probe budget is one management probe per (channel, name) with a single spaced retry when the
answer looks like upstream load-shed (429/503), plus one relay per name and a single spaced retry
on the two names this write alone is responsible for. Burst short-prompt probing got the yanglechu
key (ch206) banned upstream; these are $500 buckets on a shared box with an expiry date, spent the
same way.

Default is a dry run. --apply snapshots the DB, creates all three channels disabled, probes every
name, waits for the channel cache ONCE for the whole batch, enables all three, waits again once,
then asserts the abilities row set and relays one request per name. --teardown reverses it.
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
SECRET_DIR = Path.home() / ".new-api-local"

BASE_URL = "https://metapi.lilililwan.xyz"
CHANNELS = [("metapi", "metapi-key1.txt"), ("metapi-2", "metapi-key2.txt"), ("metapi-3", "metapi-key3.txt")]
CHANNEL_TYPE = 1  # OpenAI-compatible; /v1/chat/completions answered directly
GROUP = "default"
PRIORITY = 20  # below ch203/ch208 p30 and ch15 p25: fallback tier for the shared pool
WEIGHT = 1     # three channels already triple the draw on one throttled box
AUTO_BAN = 1
CACHE_SYNC_SECONDS = 75
PROBE_SPACING_SECONDS = 20
RETRY_SPACING_SECONDS = 45

MODELS = ["deepseek-v4.1-flash", "claude-haiku-5.5", "mimo-v2.6-flash"]
# The name that joins an existing production pool: it must answer, and it must already have a
# higher enabled leg. The other two must have no pool at all (see module docstring).
POOLED_MODEL = "deepseek-v4.1-flash"
NEW_NAMES = [m for m in MODELS if m != POOLED_MODEL]
TEST_MODEL = POOLED_MODEL

SHED_MARKERS = ("429", "503", "too many", "rate limit", "overloaded", "system busy")
# What a management probe may see that says nothing about whether the name works: the upstream
# shedding, and the fork's own transient transport failure. A probe timeout is added by the
# exception path in management_probe, not by a marker.
INCONCLUSIVE_MARKERS = (*SHED_MARKERS, "do request failed")
PROBE_TIMEOUT_SECONDS = 120
# Our own fork says this when the distributor finds no enabled leg. It is the one error shape on
# the relay path that can only come from the routing this script just wrote, never from a peer.
NO_CHANNEL_MARKER = "无可用渠道"


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
                        help="delete the channels this script owns")
    parser.add_argument("--token-id", type=int, default=1,
                        help="gateway token row used for the relay probe (key never printed)")
    return parser.parse_args()


def upstream_key(filename: str) -> str:
    key = (SECRET_DIR / filename).read_text(encoding="utf-8").strip()
    if not key.startswith("sk-") or len(key) < 20:
        raise RuntimeError(f"{filename} does not look like an sk- credential")
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


def find_by_name(smoke, headers: dict[str, str], name: str) -> dict | None:
    hits = [c for c in list_channels(smoke, headers) if c.get("name") == name]
    if len(hits) > 1:
        raise RuntimeError(f"multiple {name} channels; refusing an ambiguous write")
    return hits[0] if hits else None


def channel_payload(name: str, key: str) -> dict:
    return {
        "name": name,
        "type": CHANNEL_TYPE,
        "key": key,
        "base_url": BASE_URL,
        "models": ",".join(MODELS),
        "group": GROUP,
        "test_model": TEST_MODEL,
        "priority": PRIORITY,
        "weight": WEIGHT,
        "status": 2,  # created disabled; enabled only after the pooled name answers
        "auto_ban": AUTO_BAN,
        # Ids are served verbatim, so no mapping. No header_override either: NewAPI's Go client
        # already passes this host's Cloudflare signature rule (see module docstring).
        "model_mapping": "",
    }


def create_channel(smoke, headers: dict[str, str], name: str, key: str) -> int:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/", method="POST",
        body={"mode": "multi_to_single", "channel": channel_payload(name, key)}, headers=headers)
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"{name} POST failed: HTTP {status} {str(body)[:200]}")
    created = find_by_name(smoke, headers, name)
    if not created or not isinstance(created.get("id"), int):
        raise RuntimeError(f"{name} missing/ambiguous on readback")
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


def management_probe(smoke, headers: dict[str, str], channel_id: int,
                     model: str) -> tuple[bool, bool, str]:
    """(answered, inconclusive, detail). An inconclusive verdict is not a defect.

    Two things this has to survive without raising, because both aborted a proven-good attach:
    a slow shared box that sheds (429/503), and a probe that simply takes longer than
    smoke.http_json's 15-second default. On the third run ch214's mimo-v2.6-flash test was still
    running when the client gave up, the TimeoutError escaped the probe loop, and the fail-closed
    path disabled three channels whose verdicts were all ok so far. `do request failed` is the
    fork's own transient transport error and belongs in the same class.
    """
    try:
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={model}",
            headers=headers, timeout=PROBE_TIMEOUT_SECONDS)
    except Exception as error:
        return False, True, f"probe {type(error).__name__}: {error}"
    message = ""
    if isinstance(body, dict):
        message = str(body.get("message") or "")[:140]
    detail = f"HTTP {status} {message}".strip()
    answered = status == 200 and not (isinstance(body, dict) and body.get("success") is False)
    lowered = detail.lower()
    return answered, (not answered and any(m in lowered for m in INCONCLUSIVE_MARKERS)), detail


def pool_rows(db_path: Path, models: list[str]) -> list[tuple]:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        placeholders = ",".join("?" * len(models))
        return list(con.execute(
            'SELECT model, "group", channel_id, enabled, priority, weight FROM abilities '
            f"WHERE model IN ({placeholders})", list(models)))


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    destination = backup_dir / f"new-api-before-metapi-{time.strftime('%Y%m%d-%H%M%S')}.db"
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


def assert_prerequisites(db_path: Path, own_ids: list[int]) -> None:
    """Two different premises, both asserted: one shared pool to join, two names that must be new.

    `own_ids` are this script's own channels from an earlier aborted attempt. They must be excluded
    from the "these names are new" test or a re-run can never pass: the previous attempt left its
    own disabled legs behind, and reading those as a pre-existing pool would call this attach a
    substitution of itself.
    """
    ours = set(own_ids)
    legs = [r for r in pool_rows(db_path, [POOLED_MODEL]) if r[1] == GROUP]
    live = [r for r in legs if r[3] == 1]
    if not legs or not live:
        raise RuntimeError(f"{POOLED_MODEL} has no enabled group-default leg; joining a dead pool "
                           "would advertise capacity that does not exist")
    higher = [r for r in live if r[4] > PRIORITY]
    if not higher:
        raise RuntimeError(f"p{PRIORITY} is not a fallback tier for {POOLED_MODEL}: no enabled leg "
                           "sits above it, so this channel would become primary traffic")
    print(f"  {POOLED_MODEL:<22} shared pool legs={len(legs)} enabled={len(live)} "
          f"live_ids={[r[2] for r in live]} prio={[r[4] for r in live]}")
    print("    TRAINING-EXPOSURE NOTE: enabling this write sends whatever that pool serves to an "
          "upstream that says it may train on it. Authorized explicitly; revert is --teardown.")

    clash = [r for r in pool_rows(db_path, NEW_NAMES) if r[2] not in ours]
    if clash:
        raise RuntimeError(f"these names already have legs from another channel, so declaring them "
                           f"here would be a substitution, not an add: {sorted(clash)}")
    for name in NEW_NAMES:
        carried = [r[2] for r in pool_rows(db_path, [name]) if r[2] in ours]
        print(f"  {name:<22} no foreign legs -> new single-tier pool"
              + (f" (own leftover rows on ch{carried} are the previous aborted attempt)" if carried else ""))

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
        print(f"  NOTE: {unpriced} have no ModelRatio entry and bill at the implied "
              "model_ratio=37.5. This script writes no option row; pricing stays an operator call.")


def channel_statuses(db_path: Path, channel_ids: list[int]) -> dict[int, int]:
    if not channel_ids:
        return {}
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        return {r[0]: r[1] for r in con.execute(
            f"SELECT id, status FROM channels WHERE id IN ({','.join('?' * len(channel_ids))})",
            list(channel_ids))}


def tier_key(row: tuple) -> tuple:
    """Routing posture without `enabled`: the fork derives enabled from the channel's status."""
    return (row[0], row[1], row[2], row[4], row[5])


def assert_row_set(db_path: Path, pre: list[tuple], post: list[tuple],
                   channel_ids: list[int]) -> None:
    """Every leg outside this batch must keep its tier, and each of ours must land enabled.

    Two separate questions get answered separately, and conflating them caused a wrong abort on
    2026-10-11. `enabled` is DERIVED state -- the fork writes it from the channel's own status --
    so a foreign row flipping 1 -> 0 is normally that channel being auto-banned by Guardian, not
    this write clobbering anything. When that happened mid-attach, the old guard fail-closed the
    three channels it had just proven healthy to punish an unrelated upstream outage.

    So: a foreign TIER disappearing (or changing priority/weight) is this write's fault and aborts.
    A foreign enablement drop is attributed by reading that channel's status; if the channel is no
    longer status=1 the outage is independent and it is reported, loudly, not repaired here. This
    channel's own rows are excluded from the loss test because the fork rebuilds them on enable.
    """
    ours = set(channel_ids)

    def by_tier(rows: list[tuple]) -> dict[tuple, tuple]:
        return {tier_key(r): r for r in rows}

    pre_t, post_t = by_tier(pre), by_tier(post)

    lost_tier = sorted(t for t in (set(pre_t) - set(post_t)) if t[2] not in ours)
    gained = sorted(t for t in (set(post_t) - set(pre_t)) if t[2] not in ours)
    if lost_tier:
        raise RuntimeError(f"abilities tiers REMOVED by this write (production regression): {lost_tier}")
    if gained:
        raise RuntimeError(f"abilities tiers APPEARED for other channels: {gained}")

    disabled = sorted(t for t in set(pre_t) & set(post_t)
                      if t[2] not in ours and pre_t[t][3] == 1 and post_t[t][3] != 1)
    if disabled:
        culprits = sorted({t[2] for t in disabled})
        statuses = channel_statuses(db_path, culprits)
        explained = [c for c in culprits if statuses.get(c) != 1]
        unexplained = [c for c in culprits if statuses.get(c) == 1]
        if unexplained:
            raise RuntimeError(f"legs went disabled while their channel stayed status=1 -- this is "
                               f"the signature of a write clobbering routing posture: ch{unexplained}")
        print(f"  INDEPENDENT OUTAGE (not this write): ch{explained} went disabled during the attach "
              f"window, statuses={{{', '.join(f'ch{c}={statuses[c]}' for c in explained)}}}. "
              "Their legs are re-derived from their own channel status; this batch does not repair "
              "or inherit that. Attribute it before assuming anything about the pool.")

    intended = {(m, GROUP, cid, 1, PRIORITY, WEIGHT) for m in MODELS for cid in channel_ids}
    missing = sorted(intended - set(post))
    if missing:
        raise RuntimeError(f"expected new legs did not appear: {missing}")
    rebuilt = sorted(t for t in (set(pre_t) - set(post_t)) if t[2] in ours)
    print(f"  row set ok: {len([t for t in post_t if t[2] in ours])} legs on ch{sorted(ours)}, "
          f"{len(pre)} pre tiers intact"
          + (f", own pre-disable tiers rebuilt: {rebuilt}" if rebuilt else ""))


def gateway_key(db_path: Path, token_id: int) -> str:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        row = con.execute("SELECT key FROM tokens WHERE id = ?", (token_id,)).fetchone()
    if not row or not row[0]:
        raise RuntimeError(f"token id {token_id} missing or keyless")
    return str(row[0]).strip()


def relay_probe(smoke, key: str, model: str) -> dict:
    """One non-stream request through the gateway, returned as a verdict instead of raised.

    Raising here aborted the whole attach on the first pool-mate's 429 and then fail-closed the
    three channels the management probes had just proven -- the same mis-attribution the abilities
    guard used to make, one stage later. On a shared pool the relay picks whichever enabled leg
    wins the draw, so for deepseek-v4.1-flash that is always a tier ABOVE these channels and the
    answer (or error) describes that leg, not metapi. Only the two names whose sole legs are these
    channels carry information about this write.
    """
    body = {"model": model, "max_tokens": 64,
            "messages": [{"role": "user", "content": "Reply with exactly OK"}], "stream": False}
    request = urllib.request.Request(
        f"{smoke.NEWAPI_BASE}/v1/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}, method="POST")
    t0 = time.time()
    detail = ""
    parsed: dict = {}
    try:
        with urllib.request.urlopen(request, timeout=170) as response:
            parsed = json.loads(response.read().decode("utf-8", "replace"))
        answered = True
    except urllib.error.HTTPError as error:
        answered = False
        detail = f"HTTP {error.code} {error.read().decode('utf-8', 'replace')[:200]}"
    except Exception as error:
        answered = False
        detail = f"{type(error).__name__}: {error}"
    lowered = detail.lower()
    choice = (parsed.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content")
    return {"model": model, "answered": answered and bool(content), "shed": not answered
            and any(m in lowered for m in SHED_MARKERS), "no_channel": not answered
            and NO_CHANNEL_MARKER in detail, "content": content,
            "finish": choice.get("finish_reason"), "usage": parsed.get("usage"),
            "detail": detail.strip() or ("empty content" if answered else "no answer"),
            "sec": round(time.time() - t0, 2)}


def log_rows(db_path: Path, channel_ids: list[int], since: int) -> list[dict]:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id, channel_id, model_name, type, quota, prompt_tokens, completion_tokens, "
            "created_at FROM logs WHERE created_at >= ? ORDER BY id DESC LIMIT 200", (since,)).fetchall()
    return [dict(r) for r in rows if r["channel_id"] in set(channel_ids)]


def main() -> int:
    args = parse_args()
    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}
    keys = {name: upstream_key(filename) for name, filename in CHANNELS}

    existing = {name: find_by_name(smoke, headers, name) for name, _ in CHANNELS}
    channel_ids = {name: int(row["id"]) for name, row in existing.items() if row}
    live_ids = sorted(channel_ids.values())

    if args.teardown:
        if not channel_ids:
            print("no metapi channels to tear down")
            return 0
        if not args.apply:
            print(f"dry-run: would DELETE {sorted(channel_ids.values())}")
            return 0
        backup = online_backup(db_path)
        print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")
        for name in sorted(channel_ids):
            delete_channel(smoke, headers, channel_ids[name])
            print(f"  deleted {name} ch{channel_ids[name]}")
        print(f"OK: {len(channel_ids)} channels deleted. Recovery snapshot: {backup.name}")
        return 0

    for name, row in existing.items():
        if row and (row.get("base_url") != BASE_URL or row.get("type") != CHANNEL_TYPE
                    or row.get("group") != GROUP):
            raise RuntimeError(f"existing {name} channel no longer matches the inspected contract")

    plan = f"use existing {sorted(channel_ids.values())}" if channel_ids \
        else f"create {[n for n, _ in CHANNELS]}"
    print(f"plan: {plan}; models {MODELS}; group {GROUP}; priority {PRIORITY} weight {WEIGHT} "
          f"auto_ban {AUTO_BAN}; type {CHANNEL_TYPE}; model_mapping empty; base {BASE_URL}")
    for name, key in keys.items():
        print(f"  {name:<10} key {redact(key)} (value never printed)")
    print("pool premise")
    assert_prerequisites(db_path, live_ids)

    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")
    pre = pool_rows(db_path, MODELS)
    started = int(time.time())
    live_ids = sorted(channel_ids.values())
    try:
        if not channel_ids:
            for name, _ in CHANNELS:
                cid = create_channel(smoke, headers, name, keys[name])
                channel_ids[name] = cid
                print(f"  created {name} ch{cid} disabled")
            live_ids = sorted(channel_ids.values())

        verdicts: dict[int, dict[str, str]] = {}
        for name in sorted(channel_ids):
            cid = channel_ids[name]
            verdicts[cid] = {}
            print(f"management probes on ch{cid} ({name})")
            first = True
            for model in MODELS:
                if not first:
                    time.sleep(PROBE_SPACING_SECONDS)
                first = False
                answered, inconclusive, detail = management_probe(smoke, headers, cid, model)
                if not answered and inconclusive:
                    print(f"  {model:<22} inconclusive {detail} -> one spaced retry")
                    time.sleep(RETRY_SPACING_SECONDS)
                    answered, inconclusive_again, detail = management_probe(smoke, headers, cid, model)
                    verdicts[cid][model] = ("ok" if answered
                                           else "INCONCLUSIVE-still" if inconclusive_again
                                           else f"FAIL {detail}")
                else:
                    verdicts[cid][model] = "ok" if answered else f"FAIL {detail}"
                print(f"  {model:<22} {verdicts[cid][model]}")

        answered_any = {cid: any(v == "ok" for v in verdicts[cid].values()) for cid in live_ids}
        if not any(answered_any.values()):
            raise RuntimeError(f"no name answered on any channel: {verdicts}")
        pooled = {cid: verdicts[cid][POOLED_MODEL] for cid in live_ids}
        if not any(v == "ok" for v in pooled.values()):
            raise RuntimeError(f"{POOLED_MODEL} never answered on any channel: {pooled}. Enabling "
                               "a leg into a live shared pool on a name that does not answer would "
                               "advertise capacity that is not there.")
        unconfirmed = {cid: [m for m, v in verdicts[cid].items() if v != "ok"]
                       for cid in live_ids if any(v != "ok" for v in verdicts[cid].values())}
        if unconfirmed:
            print(f"  WARN: these (channel, name) pairs are declared but not confirmed this run: "
                  f"{unconfirmed}. An inconclusive verdict is a throttle or a slow hop on that key, "
                  f"not a dead model; they stay declared because /v1/models lists all three ids on "
                  f"every key.")

        print(f"waiting {CACHE_SYNC_SECONDS}s for the channel cache (batch of {len(live_ids)})", flush=True)
        time.sleep(CACHE_SYNC_SECONDS)
        for cid in live_ids:
            set_status(smoke, headers, cid, 1)
        print(f"ch{live_ids} enabled; waiting {CACHE_SYNC_SECONDS}s for abilities to route", flush=True)
        time.sleep(CACHE_SYNC_SECONDS)

        post = pool_rows(db_path, MODELS)
        print("abilities regression guard")
        assert_row_set(db_path, pre, post, live_ids)

        gkey = gateway_key(db_path, args.token_id)
        print(f"relay probes via token{args.token_id} sha12="
              f"{hashlib.sha256(gkey.encode()).hexdigest()[:12]}")
        for model in MODELS:
            outcome = relay_probe(smoke, gkey, model)
            if not outcome["answered"] and model in NEW_NAMES:
                # Only our own names get a retry. Re-probing the pooled name would spend a
                # pool-mate's cap, and ch203 is already at a weekly limit.
                print(f"  relay {model}: {outcome['detail'][:90]} -> one spaced retry")
                time.sleep(RETRY_SPACING_SECONDS)
                outcome = relay_probe(smoke, gkey, model)
            shared = model == POOLED_MODEL
            if outcome["no_channel"]:
                # The distributor found no enabled leg. On a name whose only legs are these three
                # channels that can only be this write; on the pooled name it predates us.
                if shared:
                    print(f"  WARN relay {model}: {outcome['detail'][:160]} -- the pool above "
                          "these legs is short of capacity. Not this write's verdict; the tier "
                          "guard already proved the row set.")
                else:
                    raise RuntimeError(f"relay {model}: the gateway cannot route a name whose "
                                       f"only legs are these channels: {outcome['detail'][:200]}")
            elif not outcome["answered"]:
                reason = "throttled" if outcome["shed"] else "hard failure"
                if shared:
                    print(f"  WARN relay {model}: {reason}, {outcome['detail'][:160]} -- this is "
                          "a pool-mate's answer, not metapi's. The pooled name is served by a "
                          "higher tier by design, so the relay cannot reach these legs at all.")
                elif outcome["shed"]:
                    print(f"  WARN relay {model}: throttled ({outcome['detail'][:160]}) -- the "
                          "management probes answered this name on at least one key, so this is "
                          "the per-key throttle the module docstring describes, not a dead name.")
                else:
                    raise RuntimeError(f"relay {model}: {reason} on a name whose only legs are "
                                       f"these channels: {outcome['detail'][:200]}")
            else:
                print(f"  relay {model}: ok content={str(outcome['content'])[:40]!r} "
                      f"finish={outcome['finish']} usage={outcome['usage']} {outcome['sec']}s")
            time.sleep(10)

        rows = log_rows(db_path, live_ids, started)
        served = {}
        for r in rows:
            if r["type"] == 2 and r["completion_tokens"] > 0:
                served.setdefault(r["channel_id"], set()).add(r["model_name"])
        unbilled = [r["id"] for r in rows
                    if r["type"] == 2 and not r["quota"] and not r["prompt_tokens"]
                    and not r["completion_tokens"]]
        attribution = ", ".join(f"ch{c}={sorted(m)}" for c, m in sorted(served.items()))
        print(f"log attribution: {attribution or 'none'} "
              f"(rows on this batch={len(rows)}, unbilled empty rounds={unbilled or 'none'})")
        print(f"OK: ch{live_ids} live at priority {PRIORITY}. Snapshot: {backup.name}")
        print("NOTE: relay coverage is unequal by design. A new name only reaches this line after "
              "answering through the gateway, so the two new names are proven end to end here (or "
              "reported above as throttled). The pooled name is not proven at all: p20 sits below "
              "ch203/ch208/ch15 and the relay cannot select it, so whatever it answers belongs to a "
              "leg above. What the pooled attach does prove is the abilities row and the management "
              "probe. None of this verifies the upstream is not serving different weights under "
              "these ids. Two consequences stand: every prompt the default pool serves for "
              "deepseek-v4.1-flash can now reach an upstream that says it may train on it, and this "
              "whole attach expires at 09:00 tomorrow -- teardown, not revival, is the end state "
              "when the keys go.")
        return 0
    except Exception as error:
        for cid in live_ids:
            try:
                set_status(smoke, headers, cid, 2)
                print(f"ch{cid} disabled as the fail-closed step")
            except Exception as disable_error:
                print(f"WARN: could not disable ch{cid}: {disable_error}")
        print(f"registration failed: {str(error)[:400]}\nRecovery snapshot: {backup.name}")
        raise


if __name__ == "__main__":
    sys.exit(main())
