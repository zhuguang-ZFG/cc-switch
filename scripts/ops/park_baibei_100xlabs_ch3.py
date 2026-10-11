"""Park ch3 baibei-100xlabs out of rotation, or restore it.

Why this channel and not the others: it is the ONLY channel carrying
claude-opus-5 / claude-opus-5-5 / claude-fable-5-1 / claude-fable-5.1 that is still
enabled, it sits at the top of that pool (priority 52, weight 20, above every peer at
50), and its upstream has answered 503 "All available accounts exhausted" for all four
names since 10-07. auto_ban=0 and its last recorded status change is 10-06, so nothing
in NewAPI or the Guardian will ever retire it on its own -- it just adds a dead 4-7s hop
in front of every request for those names and blocks the tier when a peer recovers.

Parked, not deleted: abilities go enabled=0 with the channel and come back on restore,
which is what today's ch86/134/135/136 enable-disable cycles demonstrated.

Dry-run by default. --apply takes an online snapshot, verifies it, then writes.
--restore puts it back.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

CHANNEL_NAME = "baibei-100xlabs"
CHANNEL_ID = 3
AFFECTED_MODELS = ["claude-opus-5", "claude-opus-5-5", "claude-fable-5-1", "claude-fable-5.1"]
CACHE_SYNC_SECONDS = 75
HOME = Path("C:/Users/zhugu/.new-api-local")

HERE = Path(__file__).resolve()
SCRIPTS = HERE.parent


def load(name: str, filename: str):
    path = SCRIPTS / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert module is not None and spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually write (default is dry-run)")
    parser.add_argument("--restore", action="store_true", help="set ch3 back to enabled")
    return parser.parse_args()


def key_fingerprint(db_path: Path) -> tuple[int, str]:
    """Length + digest of the stored key, never the key itself.

    The management API view redacts keys in this fork, so the only way to prove a
    status write left the credential alone is to compare it in the database.
    """
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        key = con.execute("SELECT key FROM channels WHERE id=?", (CHANNEL_ID,)).fetchone()[0] or ""
    return len(key), hashlib.sha256(str(key).encode()).hexdigest()[:12]


def channel_state(smoke, headers: dict[str, str]) -> dict:
    status, body = smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/{CHANNEL_ID}", headers=headers)
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"cannot read ch{CHANNEL_ID}: HTTP {status}")
    data = body.get("data") or {}
    return {"status": data.get("status"), "name": data.get("name"),
            "priority": data.get("priority"), "weight": data.get("weight"),
            "auto_ban": data.get("auto_ban"), "models": data.get("models"),
            "group": data.get("group")}


def ability_rows(db_path: Path) -> list[tuple]:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        return con.execute("""SELECT model, "group", enabled, priority, weight
                              FROM abilities WHERE channel_id=? ORDER BY model, "group" """,
                           (CHANNEL_ID,)).fetchall()


def routable_names(db_path: Path) -> dict[str, list[str]]:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        out = {}
        for model in AFFECTED_MODELS:
            rows = con.execute("""SELECT a.channel_id, c.name, a.priority, a.weight
                                  FROM abilities a JOIN channels c ON c.id = a.channel_id
                                  WHERE a.model=? AND a.enabled=1 AND c.status=1 AND a."group"='default'
                                  ORDER BY a.priority DESC""", (model,)).fetchall()
            out[model] = [f"ch{r[0]}({r[1]})p{r[2]}w{r[3]}" for r in rows]
        return out


def online_backup(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = HOME / f"new-api-before-park-ch3-{stamp}.db"
    with closing(sqlite3.connect(db_path.as_posix(), timeout=30)) as src, \
            closing(sqlite3.connect(target.as_posix())) as dst:
        src.backup(dst)
    with closing(sqlite3.connect(target.as_posix())) as check:
        verdict = check.execute("PRAGMA integrity_check").fetchone()[0]
    if verdict != "ok":
        raise RuntimeError(f"backup failed integrity check: {verdict}")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()[:12]
    print(f"backup ok: {target.name} ({target.stat().st_size} bytes, integrity=ok, sha256={digest})")
    return target


def set_status(smoke, headers: dict[str, str], status: int) -> None:
    response, body = smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/{CHANNEL_ID}/status",
                                     method="POST", body={"status": status}, headers=headers)
    if response != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"status POST failed: HTTP {response} body={body}")


def main() -> int:
    args = parse_args()
    smoke = load("smoke", "newapi-local-smoke.py")
    db_path = Path(smoke.NEWAPI_DB).resolve()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    want = 1 if args.restore else 2
    verb = "RESTORE" if args.restore else "PARK"

    before = channel_state(smoke, headers)
    if before["name"] != CHANNEL_NAME:
        raise RuntimeError(f"ch{CHANNEL_ID} is {before['name']!r}, expected {CHANNEL_NAME!r}")
    key_before = key_fingerprint(db_path)
    if not key_before[0]:
        raise RuntimeError(f"ch{CHANNEL_ID} has no stored key; refusing to write")
    print(f"ch{CHANNEL_ID} {CHANNEL_NAME}: status={before['status']} p={before['priority']} "
          f"w={before['weight']} auto_ban={before['auto_ban']} group={before['group']}")
    print(f"   models={before['models']}")
    print(f"   stored key: len={key_before[0]} sha256={key_before[1]}")

    if args.apply:
        online_backup(db_path)

    print(f"abilities before: {ability_rows(db_path)}")
    print(f"routable before:")
    for model, chans in routable_names(db_path).items():
        print(f"   {model:<18} {chans or '(none)'}")

    if before["status"] == want:
        print(f"nothing to do: ch{CHANNEL_ID} already status={want}")
        return 0

    if not args.apply:
        print(f"dry-run: would POST /api/channel/{CHANNEL_ID}/status {{'status': {want}}} ({verb})")
        print(f"         re-run with --apply")
        return 0

    set_status(smoke, headers, want)
    print(f"{verb} accepted; waiting {CACHE_SYNC_SECONDS}s for the channel cache and abilities")
    time.sleep(CACHE_SYNC_SECONDS)

    after = channel_state(smoke, headers)
    print(f"ch{CHANNEL_ID} now status={after['status']} (expected {want})")
    if after["status"] != want:
        raise RuntimeError("status did not stick")
    key_after = key_fingerprint(db_path)
    if not key_after[0]:
        raise RuntimeError(f"key vanished during the status write; restore from the snapshot above")
    if key_after != key_before:
        print(f"   WARN stored key differs after the write ({key_before} -> {key_after}); "
              f"this upstream stores a refresh chain, so rotation is the likely cause")
    else:
        print(f"   stored key unchanged: len={key_after[0]} sha256={key_after[1]}")
    print(f"abilities after: {ability_rows(db_path)}")
    print(f"routable after:")
    for model, chans in routable_names(db_path).items():
        print(f"   {model:<18} {chans or '(none - parked)'}")

    print(f"\nrollback: python {HERE.name} --restore --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
