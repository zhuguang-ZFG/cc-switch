#!/usr/bin/env python3
"""Register the yanglechu ('初api') endpoint as a new local NewAPI channel.

Upstream is https://api.yanglechu.com (the fork appends /v1/chat/completions). The
credential lives only in ~/.new-api-local/yanglechu-key.txt and is never printed.

GET /v1/models lists 14 names. Nine answer /chat/completions; the five gpt-image-*
names return 503 there because they are image endpoints, so they are not registered:
a chat-type channel carrying names that cannot serve chat produces dead selectors.

The nine registered names are used verbatim, as asked, which places this channel in
existing group-default pools. Measured posture of the enabled members:

    codex-auto-review  ch175 p=-20 w=1
    gpt-5.5            ch175 ch188 ch189 ch190 p=-20 w=1
    gpt-5.6-luna       ch199 p=20, ch174 p=-10, ch188.. p=-20
    gpt-5.6-sol        ch127 p=40, ch175 ch188.. p=-20
    gpt-5.6-terra      ch174 p=-10, ch175 ch188.. p=-20
    gpt-6-astra        ch127 p=40, ch182 p=20, ch175 ch188.. p=-20
    gpt-6-luna         ch200 p=20, ch188.. p=-20
    gpt-6-sol          ch188.. p=-20
    gpt-6.1-sol        ch175 ch188.. p=-20

NewAPI selects the highest priority first and only then weights within it, so joining
at the fork's UI defaults (priority 0, weight 5) would make this channel the preferred
target for six of the nine models. That is a takeover, not a pool membership, so this
channel joins at priority -20 weight 1: it sits beside the existing tail members and
adds capacity while shifting each peer's share only marginally.

Because a gateway relay for a shared name can legitimately be served by a
higher-priority peer, end-to-end evidence is reported in two layers: the channel-scoped
management probe proves this channel's own upstream path, and the gateway relay proves
the name routes. Attribution counts are reported, not asserted.

Default is a dry run. --apply snapshots the DB, creates the channel disabled, probes
every model, waits for the channel cache, enables, then relays through the gateway.
--teardown reverses it. Any failure disables the new channel.
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
KEY_PATH = Path.home() / ".new-api-local" / "yanglechu-key.txt"

CHANNEL_NAME = "yanglechu"
BASE_URL = "https://api.yanglechu.com"
GROUP = "default"
PRIORITY = -20
WEIGHT = 1
AUTO_BAN = 1
CACHE_SYNC_SECONDS = 75
MODELS = [
    "codex-auto-review",
    "gpt-5.5",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-6-astra",
    "gpt-6-luna",
    "gpt-6-sol",
    "gpt-6.1-sol",
]
TEST_MODEL = "gpt-5.5"
# Names to prove through the gateway: the two this deployment's Codex config uses.
RELAY_MODELS = ["gpt-6-astra", "gpt-5.5"]


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
    parser.add_argument("--teardown", action="store_true", help="delete this channel")
    parser.add_argument("--token-id", type=int, default=1,
                        help="gateway token row used for the relay probe (key never printed)")
    parser.add_argument("--relay-attempts", type=int, default=6,
                        help="gateway calls per relay model; a shared name may be served by a peer")
    return parser.parse_args()


def upstream_key() -> str:
    key = KEY_PATH.read_text(encoding="utf-8").strip()
    if len(key) < 20:
        raise RuntimeError(f"{KEY_PATH.name} looks truncated")
    return key


def redact(key: str) -> str:
    return f"len={len(key)} sha12={hashlib.sha256(key.encode()).hexdigest()[:12]}"


def upstream_models(key: str) -> list[str]:
    request = urllib.request.Request(f"{BASE_URL}/v1/models",
                                     headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return sorted(m["id"] for m in json.loads(response.read()).get("data") or [])


def upstream_probe(key: str, model: str, attempts: int = 4) -> tuple[str, str]:
    """429 is a rate limit, not a dead model: it is retried with backoff, and the
    probes are spaced, because this script itself can trip the limiter it is judging.
    Any other HTTP status is deterministic and returned at once."""
    body = {"model": model, "max_tokens": 256,
            "messages": [{"role": "user", "content": "Reply with exactly OK"}]}
    last = ("exc", "no attempt ran")
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            f"{BASE_URL}/v1/chat/completions", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                parsed = json.loads(response.read())
            choice = (parsed.get("choices") or [{}])[0]
            content = ((choice.get("message") or {}).get("content") or "").strip()
            if content:
                return "ok", content[:20]
            # 200 with finish_reason=length is a live route that spent its budget.
            return "empty", f"finish={choice.get('finish_reason')}"
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace").replace("\n", " ")[:70]
            if error.code == 429 and attempt < attempts:
                time.sleep(5 * attempt)
                continue
            return f"HTTP {error.code}", detail
        except Exception as error:
            last = ("transport", f"{type(error).__name__}: {str(error)[:60]}")
            if attempt < attempts:
                time.sleep(2)
    return last


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
        "type": 1,
        "key": key,
        "base_url": BASE_URL,
        "models": ",".join(MODELS),
        "group": GROUP,
        "test_model": TEST_MODEL,
        "priority": PRIORITY,
        "weight": WEIGHT,
        "status": 2,
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
        raise RuntimeError(f"status POST failed: HTTP {response_status}")


def delete_channel(smoke, headers: dict[str, str], channel_id: int) -> None:
    response_status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}", method="DELETE", headers=headers)
    if response_status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"channel DELETE failed: HTTP {response_status}")


def management_probe(smoke, headers: dict[str, str], channel_id: int, model: str) -> bool:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={model}",
        headers=headers, timeout=170)
    text = json.dumps(body) if isinstance(body, (dict, list)) else str(body)
    ok = status == 200 and isinstance(body, dict) and bool(body.get("success"))
    print(f"  probe {model:<20} {'ok' if ok else 'FAIL'} "
          f"time={body.get('time') if isinstance(body, dict) else '-'} {'' if ok else text[:130]}")
    return ok


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    destination = backup_dir / f"new-api-before-yanglechu-{time.strftime('%Y%m%d-%H%M%S')}.db"
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


def assert_prerequisites(smoke, headers, db_path: Path, key: str) -> None:
    listed = upstream_models(key)
    print(f"  upstream /v1/models: {len(listed)} names")
    unexpected = [m for m in MODELS if m not in listed]
    if unexpected:
        raise RuntimeError(f"registered names not offered upstream: {unexpected}")
    skipped = [m for m in listed if m not in MODELS]
    print(f"  not registered (chat-unreachable): {skipped}")

    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        ratios = json.loads((con.execute("SELECT value FROM options WHERE key='ModelRatio'").fetchone()[0]))
        missing_ratio = [m for m in MODELS if m not in ratios]
        print(f"  ModelRatio missing for: {missing_ratio or 'none'} (this script writes no option)")
        print(f"  pool joined per model (enabled, default group):")
        for model in MODELS:
            rows = con.execute(
                'SELECT a.channel_id, c.name, a.priority, a.weight FROM abilities a '
                "JOIN channels c ON c.id = a.channel_id "
                "WHERE a.model = ? AND a.\"group\" = 'default' AND a.enabled = 1 AND c.status = 1 "
                "ORDER BY a.priority DESC", (model,)).fetchall()
            peers = ", ".join(f"ch{r[0]}({r[1]},p={r[2]},w={r[3]})" for r in rows) or "NONE"
            better = [r for r in rows if int(r[2]) > PRIORITY]
            print(f"    {model:<20} this ch p={PRIORITY} w={WEIGHT} | peers {peers[:60]}"
                  f" | outranked by {len(better)}")

    print("  upstream chat check (keys not printed)")
    failures = {}
    for model in MODELS:
        verdict, detail = upstream_probe(key, model)
        print(f"    {model:<20} {verdict} {detail}")
        if verdict not in ("ok", "empty"):
            failures[model] = f"{verdict} {detail}"
    if failures:
        # Passing 14 models back to back trips this upstream's own pool cooldown, so a
        # refusal in the burst is not evidence. Re-check the refused names alone, calmly.
        print(f"  re-checking {sorted(failures)} calmly (60s then 20s apart)")
        time.sleep(60)
        for model in sorted(failures):
            verdict, detail = upstream_probe(key, model, attempts=2)
            print(f"    {model:<20} recheck {verdict} {detail}")
            if verdict in ("ok", "empty"):
                del failures[model]
            else:
                failures[model] = f"{verdict} {detail}"
            time.sleep(20)
    # A lingering 429 after the calm pass is upstream capacity, not a bad name or key:
    # registering is the point of a last-resort channel, and 429 is not an auto-disable
    # code here (AutomaticDisableStatusCodes = 401,402,403,502), so it cannot bankrupt
    # the channel. 401/403/404 are deterministic and do refuse the registration.
    hard = {m: d for m, d in failures.items() if not d.startswith("HTTP 429")}
    if hard:
        raise RuntimeError(f"upstream refused {sorted(hard)}; not registering a partly-dead pool")
    for model, detail in sorted(failures.items()):
        print(f"  WARN {model} still capacity-limited: {detail}")


def gateway_key(db_path: Path, token_id: int) -> str:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        row = con.execute("SELECT key FROM tokens WHERE id = ?", (token_id,)).fetchone()
    if not row or not row[0]:
        raise RuntimeError(f"token id {token_id} missing or keyless")
    return str(row[0]).strip()


def relay_probe(smoke, key: str, model: str, stream: bool) -> dict:
    """Return a verdict; never raise.

    This channel sits at the bottom of every shared pool, so a gateway request for a
    shared name is served by a higher-priority peer. A peer's upstream refusing the
    request (e.g. its own anti-probing guard on a short prompt) says nothing about this
    channel, so it must not abort the cutover. The channel-scoped management probe is
    what proves this channel's own path.
    """
    body = {"model": model, "max_tokens": 256,
            "messages": [{"role": "user", "content": "Reply with exactly OK"}], "stream": stream}
    request = urllib.request.Request(
        f"{smoke.NEWAPI_BASE}/v1/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(request, timeout=170) as response:
            raw, status = response.read().decode("utf-8", "replace"), response.status
    except urllib.error.HTTPError as error:
        return {"model": model, "stream": stream, "http": error.code, "ok": False,
                "content": None, "finish": None, "sec": round(time.time() - t0, 2),
                "error": error.read().decode("utf-8", "replace").replace("\n", " ")[:120]}
    except Exception as error:
        return {"model": model, "stream": stream, "http": "EXC", "ok": False, "content": None,
                "finish": None, "sec": round(time.time() - t0, 2),
                "error": f"{type(error).__name__}: {str(error)[:110]}"}
    if stream:
        data = [l for l in raw.splitlines() if l.startswith("data:")]
        bad = not data or '"error"' in raw or not raw.rstrip().endswith("[DONE]")
        return {"model": model, "stream": True, "http": status, "ok": not bad,
                "content": f"{len(data)} chunks", "finish": None,
                "sec": round(time.time() - t0, 2),
                "error": None if not bad else raw[:120].replace("\n", " ")}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"model": model, "stream": False, "http": status, "ok": False, "content": None,
                "finish": None, "sec": round(time.time() - t0, 2), "error": f"NOT-JSON {raw[:110]}"}
    choice = (parsed.get("choices") or [{}])[0]
    content = ((choice.get("message") or {}).get("content") or "")
    return {"model": model, "stream": False, "http": status, "ok": bool(content.strip()),
            "content": content[:30] or None,
            "finish": choice.get("finish_reason"), "sec": round(time.time() - t0, 2),
            "error": None if content.strip() else "empty content"}


def attribution(db_path: Path, channel_id: int, models: list[str], since: int) -> list[dict]:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id, model_name, type, quota, completion_tokens, is_stream FROM logs "
            "WHERE channel_id = ? AND model_name IN ({}) AND created_at >= ? ORDER BY id DESC LIMIT 20"
            .format(",".join("?" * len(models))), (channel_id, *models, since)).fetchall()
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
            print("no yanglechu channel to tear down")
            return 0
        if not args.apply:
            print(f"dry-run: would DELETE ch{channel_id}")
            return 0
        backup = online_backup(db_path)
        print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")
        delete_channel(smoke, headers, channel_id)
        print(f"OK: ch{channel_id} deleted. Recovery snapshot: {backup.name}")
        return 0

    if existing and (existing.get("base_url") != BASE_URL or existing.get("type") != 1
                     or existing.get("group") != GROUP):
        raise RuntimeError("existing yanglechu channel no longer matches the inspected contract")

    print(f"plan: {'set ch' + str(channel_id) if channel_id else 'create ' + CHANNEL_NAME}; "
          f"{len(MODELS)} models verbatim; group {GROUP}; priority {PRIORITY} weight {WEIGHT} "
          f"auto_ban {AUTO_BAN}; key {redact(key)} (value never printed)")
    assert_prerequisites(smoke, headers, db_path, key)

    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")
    started = int(time.time())
    try:
        if channel_id is None:
            channel_id = create_channel(smoke, headers, key)
            print(f"created ch{channel_id} disabled")
        print("management probes (channel-scoped: this channel's own upstream path)")
        results = {m: management_probe(smoke, headers, channel_id, m) for m in MODELS}
        passed = sorted(m for m, ok in results.items() if ok)
        if not passed:
            raise RuntimeError(f"no model answered on ch{channel_id}: {results}")
        print(f"waiting {CACHE_SYNC_SECONDS}s for the channel cache", flush=True)
        time.sleep(CACHE_SYNC_SECONDS)
        set_status(smoke, headers, channel_id, 1)
        print(f"ch{channel_id} enabled; waiting {CACHE_SYNC_SECONDS}s for abilities to route", flush=True)
        time.sleep(CACHE_SYNC_SECONDS)

        gkey = gateway_key(db_path, args.token_id)
        print(f"gateway relay probes via token{args.token_id} "
              f"sha12={hashlib.sha256(gkey.encode()).hexdigest()[:12]} "
              f"({args.relay_attempts} attempts/model; a shared name may be served by a peer)")
        relays = []
        for model in RELAY_MODELS:
            for attempt in range(args.relay_attempts):
                r = relay_probe(smoke, gkey, model, bool(attempt % 2))
                relays.append(r)
                flag = "" if r["ok"] else f"  <- {r['error']}"
                print(f"  relay {model} stream={r['stream']}: HTTP {r['http']} "
                      f"content={r['content']} finish={r['finish']} {r['sec']}s{flag}")
                time.sleep(1)
        good = [r for r in relays if r["ok"]]
        if len(good) < 4:
            raise RuntimeError(f"gateway relay produced only {len(good)} clean answers of "
                               f"{len(relays)}; the gateway path itself is not working")
        by_model = {}
        for r in relays:
            by_model.setdefault(r["model"], []).append(r["ok"])
        for model, oks in sorted(by_model.items()):
            if not any(oks):
                print(f"  WARN relay {model}: no clean gateway answer in {len(oks)} attempts "
                      f"(a higher-priority peer owns this name; ch{channel_id} is proven by "
                      f"its management probe, not by this shared-name request)")

        time.sleep(3)
        rows = attribution(db_path, channel_id, RELAY_MODELS, started)
        print(f"attribution: {len(rows)} log row(s) on ch{channel_id} since this run "
              f"(gateway prefers higher-priority peers, so 0 is possible and is not a failure)")
        for r in rows[:6]:
            print(f"   log {r['id']} {r['model_name']} type={r['type']} completion_tokens={r['completion_tokens']}")
        print(f"OK: ch{channel_id} live. Management probes passed for {len(passed)}/{len(MODELS)} models. "
              f"Snapshot: {backup.name}")
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
    sys.exit(main())
