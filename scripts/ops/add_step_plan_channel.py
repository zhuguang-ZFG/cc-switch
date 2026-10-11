#!/usr/bin/env python3
"""Register the Step plan endpoint as a new local NewAPI channel.

Upstream is https://api.stepfun.ai/step_plan (the fork appends /v1/chat/completions).
The credential lives only in ~/.new-api-local/step-plan-key.txt as a base58 blob whose
decoded form is two keys separated by a newline; the first is used and never printed.

Model names are taken verbatim from GET /step_plan/v1/models with no mapping, because
this fork round-trips key:"" through a channel PUT and would blank a stored key if a
later edit had to re-PUT the row. All six names already exist in ModelRatio, so this
script writes no option at all.

step-5-preview is already served by ch184 (opencode-go-step-5-preview) in group
default. Adding the same name to the same group puts this channel in that pool:
ch184's own row is untouched, but its traffic share changes. That is the point of the
request, not a side effect to discover later, so it is asserted before any change.

stepaudio-2.5-asr/-tts are listed by /models but 404 on /chat/completions. They are
registered because they were asked for; the management probe reports them honestly
instead of letting a dead name look healthy.

Default is a dry run. --apply snapshots the DB, creates the channel disabled, probes
every model, waits for the channel cache, enables, then relays through the gateway and
checks log attribution. --teardown reverses it.
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
KEY_PATH = Path.home() / ".new-api-local" / "step-plan-key.txt"

CHANNEL_NAME = "step-plan"
BASE_URL = "https://api.stepfun.ai/step_plan"
GROUP = "default"
PRIORITY = 0
WEIGHT = 5
AUTO_BAN = 1
CACHE_SYNC_SECONDS = 75
MODELS = [
    "step-3.5-flash",
    "step-3.5-flash-2603",
    "step-3.7-flash",
    "step-5-preview",
    "stepaudio-2.5-asr",
    "stepaudio-2.5-tts",
]
TEST_MODEL = "step-3.7-flash"
# Verified against upstream: these two exist only on audio endpoints, never on chat.
CHAT_UNREACHABLE = {"stepaudio-2.5-asr", "stepaudio-2.5-tts"}
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


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
    """Decode the base58 blob and take the first of its two keys."""
    blob = KEY_PATH.read_text(encoding="utf-8").strip()
    n = 0
    for ch in blob:
        if ch not in B58:
            raise RuntimeError(f"{KEY_PATH.name} is not base58 (char {ch!r})")
        n = n * 58 + B58.index(ch)
    segments = n.to_bytes((n.bit_length() + 7) // 8, "big").split(b"\n")
    key = segments[0].decode("latin-1").strip()
    if len(key) < 20:
        raise RuntimeError("decoded key looks truncated")
    return key


def redact(key: str) -> str:
    return f"len={len(key)} sha12={hashlib.sha256(key.encode()).hexdigest()[:12]}"


def upstream_probe(key: str, model: str, attempts: int = 3) -> tuple[str, str]:
    """Classify a model by its verdict. An HTTP status is deterministic and returned at once;
    a transport error is retried, because an SSL flake on a model that answered minutes ago
    must not fail-close the whole registration."""
    body = {"model": model, "max_tokens": 1024,
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
            return "empty", f"finish={choice.get('finish_reason')}"
        except urllib.error.HTTPError as error:
            return f"HTTP {error.code}", error.read().decode("utf-8", "replace").replace("\n", " ")[:80]
        except Exception as error:
            last = ("transport", f"{type(error).__name__}: {str(error)[:70]}")
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
        "type": 1,  # OpenAI
        "key": key,
        "base_url": BASE_URL,
        "models": ",".join(MODELS),
        "group": GROUP,
        "test_model": TEST_MODEL,
        "priority": PRIORITY,
        "weight": WEIGHT,
        "status": 2,  # created disabled; enabled only after probes
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
    print(f"  probe {model:<22} {'ok' if ok else 'FAIL'} time={body.get('time') if isinstance(body, dict) else '-'} "
          f"{'' if ok else text[:140]}")
    return ok


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    destination = backup_dir / f"new-api-before-step-plan-{time.strftime('%Y%m%d-%H%M%S')}.db"
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
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        ratios = {}
        for option in ("ModelRatio", "CompletionRatio"):
            row = con.execute("SELECT value FROM options WHERE key = ?", (option,)).fetchone()
            ratios[option] = json.loads(row[0]) if row and row[0] else {}
        missing_ratio = [m for m in MODELS if m not in ratios["ModelRatio"]]
        # The models catalog table is empty in this deployment, so nothing here is
        # gated by a catalog row; routing today proves it. Report the fact, do not
        # invent a prerequisite the server does not enforce.
        declared = {r[0] for r in con.execute("SELECT model_name FROM models")}
        undeclared = [m for m in MODELS if m not in declared]
        catalog_rows = len(declared)
        pool = list(con.execute(
            'SELECT a.channel_id, c.name, a.enabled, a.weight, c.status FROM abilities a '
            'JOIN channels c ON c.id = a.channel_id '
            'WHERE a.model = \'step-5-preview\' AND a."group" = \'default\''))
    print(f"  ModelRatio missing for: {missing_ratio or 'none'} (this script writes no option)")
    print(f"  declared in models catalog: {catalog_rows} rows; "
          f"these six absent from catalog: {undeclared if catalog_rows else 'n/a (catalog empty, nothing gates)'}")
    print(f"  step-5-preview pool before change: {pool}")
    if not pool:
        raise RuntimeError("step-5-preview has no default-group pool; the shared-pool premise is false")
    if missing_ratio:
        raise RuntimeError(f"ratios missing for {missing_ratio}; add them deliberately, not here")

    print("  upstream chat check (keys not printed)")
    # A 200 with finish_reason=length is a working route that spent its budget on
    # reasoning, not a dead model; the fork's own management probe passes these.
    # Only an HTTP status or a transport failure may refuse the registration.
    failures = {}
    for model in MODELS:
        verdict, detail = upstream_probe(key, model)
        print(f"    {model:<22} {verdict} {detail}")
        if verdict not in ("ok", "empty"):
            failures[model] = f"{verdict} {detail}"
    dead = sorted(set(failures) - CHAT_UNREACHABLE)
    if dead:
        raise RuntimeError(f"unexpected upstream failures {dead}; refusing to register a partly-dead pool")
    expected_dead = sorted(CHAT_UNREACHABLE - set(failures))
    if expected_dead:
        raise RuntimeError(f"{expected_dead} answered chat although known to be audio-only; re-inspect")


def gateway_key(db_path: Path, token_id: int) -> str:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        row = con.execute("SELECT key FROM tokens WHERE id = ?", (token_id,)).fetchone()
    if not row or not row[0]:
        raise RuntimeError(f"token id {token_id} missing or keyless")
    return str(row[0]).strip()


def relay_probe(smoke, key: str, model: str, stream: bool) -> dict:
    body = {"model": model, "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Reply with exactly OK"}], "stream": stream}
    request = urllib.request.Request(
        f"{smoke.NEWAPI_BASE}/v1/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}, method="POST")
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
                  "content": None, "finish": None, "usage": None,
                  "sec": round(time.time() - t0, 2)}
        if not data or '"error"' in raw or not raw.rstrip().endswith("[DONE]"):
            raise RuntimeError(f"relay stream {model} produced no clean SSE: {raw[:200]}")
        return result
    parsed = json.loads(raw)
    choice = (parsed.get("choices") or [{}])[0]
    content = ((choice.get("message") or {}).get("content") or "")
    return {"model": model, "stream": False, "http": status, "content": content[:40] or None,
            "finish": choice.get("finish_reason"), "usage": parsed.get("usage"),
            "sec": round(time.time() - t0, 2)}


def log_rows(db_path: Path, channel_id: int, models: list[str]) -> list[dict]:
    since = int(time.time()) - 900
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id, model_name, channel_id, quota, prompt_tokens, completion_tokens, is_stream, use_time "
            "FROM logs WHERE channel_id = ? AND model_name IN ({}) AND created_at >= ? "
            "ORDER BY id DESC LIMIT 8".format(",".join("?" * len(models))),
            (channel_id, *models, since)).fetchall()
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
            print("no step-plan channel to tear down")
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
        raise RuntimeError("existing step-plan channel no longer matches the inspected contract")

    plan = f"set ch{channel_id}" if channel_id else f"create {CHANNEL_NAME} -> ch<new>"
    print(f"plan: {plan}; models {MODELS}; group {GROUP}; priority {PRIORITY} weight {WEIGHT} "
          f"auto_ban {AUTO_BAN}; key {redact(key)} (value never printed)")
    assert_prerequisites(smoke, headers, db_path, key)

    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")
    try:
        if channel_id is None:
            channel_id = create_channel(smoke, headers, key)
            print(f"created ch{channel_id} disabled")
        print("management probes")
        results = {m: management_probe(smoke, headers, channel_id, m) for m in MODELS}
        routed = [m for m, ok in results.items() if ok]
        if not routed:
            raise RuntimeError(f"no model answered on ch{channel_id}: {results}")
        print(f"waiting {CACHE_SYNC_SECONDS}s for the channel cache", flush=True)
        time.sleep(CACHE_SYNC_SECONDS)
        set_status(smoke, headers, channel_id, 1)
        print(f"ch{channel_id} enabled; waiting {CACHE_SYNC_SECONDS}s for abilities to route", flush=True)
        time.sleep(CACHE_SYNC_SECONDS)

        gkey = gateway_key(db_path, args.token_id)
        print(f"relay probe via token{args.token_id} "
              f"sha12={hashlib.sha256(gkey.encode()).hexdigest()[:12]}")
        relays = []
        for model in ("step-3.7-flash", "step-5-preview"):
            relays.append(relay_probe(smoke, gkey, model, False))
            relays.append(relay_probe(smoke, gkey, model, True))
            time.sleep(1)
        for r in relays:
            print(f"  relay {r['model']} stream={r['stream']}: HTTP {r['http']} "
                  f"content={r.get('content') or r.get('chunks')} finish={r.get('finish')} "
                  f"usage={r.get('usage')} {r['sec']}s")
        time.sleep(3)
        rows = log_rows(db_path, channel_id, ["step-3.7-flash", "step-5-preview"])
        if not rows:
            raise RuntimeError(f"none of the {len(relays)} relay rows attributed to ch{channel_id}")
        print(f"log attribution: {len(rows)} rows on ch{channel_id}, "
              f"zero-output {[r['id'] for r in rows if r['completion_tokens'] == 0] or 'none'}")
        print(f"OK: ch{channel_id} live. Audio-only names reported above. "
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
