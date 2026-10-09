#!/usr/bin/env python3
"""Manage the official TypeSafe Jev key pool in the existing NewAPI channel.

Jev is a structured-decision API, not a chat model. NewAPI owns the upstream
keys and polling; the loopback bridge on 8413 forwards its selected Bearer key
to api.typesafe.ai/v1/systemone. OMP uses this channel through jev_judge only.

Supply newline-separated official keys with --keys-file PATH or --keys-stdin.
Without --apply this prints a redacted plan. --apply backs up the NewAPI DB,
disables the channel, updates the key pool and full channel_info BLOB, waits for
the fork's channel cache, then requires a real management probe before enabling.
The fork cannot convert single-key channel_info through PUT; the targeted BLOB
update preserves the channel identity and avoids deleting unrelated state.

No keys supplied: verify the already configured pool without changing it.
ModelRatio=0 and auto_ban=0 retain the existing shared-pool posture. A failed
cutover leaves this channel disabled and prints the snapshot name for recovery.
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

CHANNEL_NAME = "jev-systemone-bridge"
BASE_URL = "http://127.0.0.1:8413"  # local bridge; NewAPI appends /v1/chat/completions
MODELS = ("jev-latest",)
TEST_MODEL = "jev-latest"
PRIORITY = 0
WEIGHT = 5
CACHE_SYNC_SECONDS = 75
MODEL_RATIO_OPTION = "ModelRatio"
MODEL_RATIO = 0
AUTO_BAN = 0  # shared public pool throttles; keep channel online, smoke-alert monitors
SECRETS_FILE = Path.home() / ".omp" / "guardian" / "secrets.json"
EXPECTED_ANSWER_KEYS = ("is_blocking", "urgency", "route")


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the backed-up live change; default is read-only",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--keys-file", type=Path, help="private newline-separated official key file")
    source.add_argument("--keys-stdin", action="store_true", help="read official keys from stdin")
    return parser.parse_args()


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


def post_channel(smoke, headers: dict[str, str], payload: dict) -> int:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/",
        method="POST",
        body={"mode": "multi_to_single", "channel": payload},
        headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"channel POST failed: HTTP {status}")
    items = [i for i in list_channels(smoke, headers) if i.get("name") == CHANNEL_NAME]
    if len(items) != 1 or not isinstance(items[0].get("id"), int):
        raise RuntimeError(f"created {CHANNEL_NAME} missing/ambiguous on readback")
    return int(items[0]["id"])


def set_status(smoke, headers: dict[str, str], channel_id: int, status: int) -> None:
    response_status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}/status",
        method="POST",
        body={"status": status},
        headers=headers,
    )
    if response_status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"status POST failed: HTTP {response_status}")


def channel_payload(keys: list[str]) -> dict:
    return {
        "name": CHANNEL_NAME,
        "type": 1,  # OpenAI
        "key": "\n".join(keys),
        "base_url": BASE_URL,
        "models": ",".join(MODELS),
        "group": "default",
        "test_model": TEST_MODEL,
        "priority": PRIORITY,
        "weight": WEIGHT,
        "status": 2,  # created disabled; enabled only after probes
        "auto_ban": AUTO_BAN,
        "model_mapping": "",
    }


def management_probe(smoke, headers: dict[str, str], channel_id: int, model: str) -> str:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={model}",
        headers=headers,
        timeout=170,
    )
    if status == 200 and isinstance(body, dict) and body.get("success"):
        return "ok"
    text = json.dumps(body) if isinstance(body, (dict, list)) else str(body)
    if re.search(r"(?i)rate limit|quota|429", text):
        return "quota"
    message = body.get("message") if isinstance(body, dict) else None
    raise RuntimeError(
        f"management probe failed for {model}: HTTP {status} message={message!r}"
    )


def read_omp_relay_token() -> str:
    secrets = json.loads(SECRETS_FILE.read_text(encoding="utf-8-sig"))
    token = secrets.get("newapi_probe_key")
    if not isinstance(token, str) or not token.strip():
        raise RuntimeError("guardian secrets missing newapi_probe_key")
    return token.strip()


def relay_probe(smoke, model: str) -> None:
    token = read_omp_relay_token()
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/v1/chat/completions",
        method="POST",
        body={
            "model": model,
            "messages": [{"role": "user", "content": "say OK"}],
            "max_tokens": 512,
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=170,
    )
    if status != 200 or not isinstance(body, dict):
        text = json.dumps(body) if isinstance(body, (dict, list)) else str(body)
        raise RuntimeError(f"relay probe failed for {model}: HTTP {status} {text[:200]!r}")
    choices = body.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise RuntimeError(f"relay probe for {model}: no choices in response")
    content = (choices[0].get("message") or {}).get("content")
    if not isinstance(content, str):
        raise RuntimeError(f"relay probe for {model}: message content missing")
    try:
        answers = json.loads(content)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"relay probe for {model}: answer content not JSON: {content[:120]!r}") from error
    missing = [k for k in EXPECTED_ANSWER_KEYS if k not in answers]
    if missing:
        raise RuntimeError(f"relay probe for {model}: missing answer keys {missing}: {content[:200]!r}")


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = backup_dir / f"new-api-before-jev-systemone-{stamp}.db"
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
    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        row = connection.execute(
            "SELECT value FROM options WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        return "{}"
    return row[0]


def put_option(smoke, headers: dict[str, str], key: str, value: str) -> None:
    status, _ = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/option/",
        method="PUT",
        body={"key": key, "value": value},
        headers=headers,
    )
    if status != 200:
        raise RuntimeError(f"option {key!r} update failed: HTTP {status}")


def merge_ratio(current: str, models: list[str]) -> str:
    try:
        ratios = json.loads(current)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{MODEL_RATIO_OPTION} is invalid JSON") from error
    if not isinstance(ratios, dict):
        raise RuntimeError(f"{MODEL_RATIO_OPTION} must be a JSON object")
    for model in models:
        ratios[model] = MODEL_RATIO
    return json.dumps(ratios, separators=(",", ":"), sort_keys=True)


def verify(db_path: Path, channel_id: int, ratio_value: str) -> None:
    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        ratios = json.loads(ratio_value)
        for model in MODELS:
            ability = connection.execute(
                "SELECT enabled FROM abilities WHERE channel_id = ? AND model = ?",
                (channel_id, model),
            ).fetchone()
            if ability is None:
                raise RuntimeError(f"abilities row missing for {model}")
            if ability[0] != 1:
                raise RuntimeError(f"abilities row for {model} not enabled")
            if ratios.get(model) != MODEL_RATIO:
                raise RuntimeError(f"ModelRatio for {model} != {MODEL_RATIO} on readback")


def read_pool_keys(args: argparse.Namespace) -> list[str] | None:
    if not args.keys_stdin and args.keys_file is None:
        return None
    raw = sys.stdin.read() if args.keys_stdin else args.keys_file.read_text(encoding="utf-8-sig")
    keys = list(dict.fromkeys(line.strip() for line in raw.splitlines() if line.strip()))
    if not keys or any(re.fullmatch(r"apikey_[0-9a-f]+_[0-9a-f]+", key) is None for key in keys):
        raise ValueError("key input must contain official apikey_ credentials, one per line")
    return keys


def read_pool(db_path: Path, channel_id: int) -> tuple[str, dict]:
    with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as con:
        row = con.execute("SELECT key, channel_info FROM channels WHERE id=?", (channel_id,)).fetchone()
    if row is None:
        raise RuntimeError("Jev channel missing from database")
    return row[0], json.loads(row[1] or "{}")


def verify_pool(db_path: Path, channel_id: int, keys: list[str]) -> None:
    stored, info = read_pool(db_path, channel_id)
    if stored.splitlines() != keys:
        raise RuntimeError("Jev channel key readback differs from input")
    if not (info.get("is_multi_key") is True and info.get("multi_key_size") == len(keys)
            and info.get("multi_key_mode") == "polling"
            and isinstance(info.get("multi_key_status_list"), dict)):
        raise RuntimeError("Jev multi-key polling metadata is incomplete")
    if any(status != 1 for status in info["multi_key_status_list"].values()):
        raise RuntimeError("Jev pool contains disabled credentials; inspect before enabling")


def replace_pool(db_path: Path, channel_id: int, previous: str, keys: list[str]) -> None:
    info = {
        "is_multi_key": True,
        "multi_key_size": len(keys),
        "multi_key_status_list": {},
        "multi_key_polling_index": 0,
        "multi_key_mode": "polling",
    }
    with closing(sqlite3.connect(db_path, timeout=30)) as con:
        with con:
            result = con.execute(
                "UPDATE channels SET key=?, channel_info=? WHERE id=? AND key=?",
                ("\n".join(keys), sqlite3.Binary(json.dumps(info).encode("utf-8")), channel_id, previous),
            )
            if result.rowcount != 1:
                raise RuntimeError("Jev channel changed during cutover; no keys overwritten")


def main() -> int:
    args = parse_args()
    keys = read_pool_keys(args)
    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}
    existing = [c for c in list_channels(smoke, headers) if c.get("name") == CHANNEL_NAME]
    if len(existing) > 1:
        raise RuntimeError("multiple Jev channels found; refusing ambiguous cutover")
    channel_id = int(existing[0]["id"]) if existing else None
    if existing and (existing[0].get("base_url") != BASE_URL
                     or existing[0].get("models") != ",".join(MODELS)
                     or existing[0].get("type") != 1):
        raise RuntimeError("existing Jev channel no longer matches the inspected contract")
    if keys is None:
        if channel_id is None:
            raise RuntimeError("official keys required to create the Jev channel")
        stored, _ = read_pool(db_path, channel_id)
        keys = stored.splitlines()
        verify_pool(db_path, channel_id, keys)
        print(f"plan: verify ch{channel_id}, {len(keys)} configured keys")
        if args.apply:
            if management_probe(smoke, headers, channel_id, TEST_MODEL) != "ok":
                raise RuntimeError("Jev quota probe is not a successful inference")
            relay_probe(smoke, TEST_MODEL)
            verify(db_path, channel_id, get_option_db(db_path, MODEL_RATIO_OPTION))
        return 0
    print(f"plan: {'update ch' + str(channel_id) if channel_id else 'create'} Jev pool, {len(keys)} keys")
    if not args.apply:
        print("dry-run: no changes made")
        return 0
    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")
    try:
        if channel_id is None:
            channel_id = post_channel(smoke, headers, channel_payload(keys))
        else:
            set_status(smoke, headers, channel_id, 2)
        previous, _ = read_pool(db_path, channel_id)
        replace_pool(db_path, channel_id, previous, keys)
        verify_pool(db_path, channel_id, keys)
        print(f"ch{channel_id}: {len(keys)} keys stored exactly, polling metadata verified")
        ratio = get_option_db(db_path, MODEL_RATIO_OPTION)
        if json.loads(ratio).get(TEST_MODEL) != MODEL_RATIO:
            put_option(smoke, headers, MODEL_RATIO_OPTION, merge_ratio(ratio, list(MODELS)))
        print(f"waiting {CACHE_SYNC_SECONDS}s for disabled-channel cache sync", flush=True)
        time.sleep(CACHE_SYNC_SECONDS)
        if management_probe(smoke, headers, channel_id, TEST_MODEL) != "ok":
            raise RuntimeError("Jev quota probe is not a successful inference")
        set_status(smoke, headers, channel_id, 1)
        print(f"ch{channel_id}: management inference passed; enabled", flush=True)
        time.sleep(CACHE_SYNC_SECONDS)
        relay_probe(smoke, TEST_MODEL)
        verify_pool(db_path, channel_id, keys)
        verify(db_path, channel_id, get_option_db(db_path, MODEL_RATIO_OPTION))
        print(f"OK: ch{channel_id}, {len(keys)} official keys, NewAPI relay returned decisions")
        return 0
    except Exception:
        if channel_id is not None:
            set_status(smoke, headers, channel_id, 2)
        print(f"cutover failed; Jev channel disabled. Recovery snapshot: {backup.name}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
