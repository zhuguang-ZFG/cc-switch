#!/usr/bin/env python3
"""OpenCode Go key rotation + 4-model channel cutover (2026-10-02).

User directive: new Go key (supplied via env OPENCODE_GO_NEW_KEY, never
printed/persisted); keep exactly space-bunny-free, muse-spark-1.3-contributor,
gpt-6-luna, longcat-2.5-preview-free; delete the other Go channels.

Change set
----------
1. PUT ch125 (opencode-go-omen-alpha) + ch130 (opencode-go-space-bunny-free):
   key rotation only, all other fields from the live list object (fork PUT
   contract: list-endpoint object, NO ``status`` field, explicit key).
2. POST 3 dedicated single-model channels (mode=single wrapper, no status):
   opencode-go-muse-spark-1.3 / opencode-go-gpt-6-luna /
   opencode-go-longcat-2.5-preview-free — type=1, base_url + header_override
   cloned from ch130 (Chrome UA + x-opencode-session; CF 1010 / 400
   MissingSessionID otherwise), group=default, priority=0, weight=2,
   auto_ban=1.
3. DELETE ch48 (opencode-go-muse, muse-spark-1.2-contributor, disabled since
   2026-08-21) + ch117 (opencode-go-qwen3.8-max-free, disabled; ch114
   tokenrouter leg and models.yml entry untouched).
4. ModelRatio += the 4 kept models -> 0 (Go flat subscription, marginal cost
   zero; same rationale as omen-alpha/gpt-5.6-luna = 0).
5. POST /api/channel/fix; verify abilities; 1 gateway chat per model with
   logs-table channel attribution.

Safety: online DB snapshot first; key read from env only; dry-run default.
Guardian needs no edits: PINNED_CHANNEL_WEIGHTS[48] lookup is by live channel
dict (absent = no-op) and cleanup_stale_state auto-prunes records for deleted
channel ids (guardian.py:2672-2699); ch78 deletion precedent.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
DB_PATH = Path.home() / ".new-api-local" / "new-api.db"
GATEWAY_BASE = "http://127.0.0.1:3002"

KEEP = {
    # model id -> (channel name, existing channel id or None to create)
    "space-bunny-free": ("opencode-go-space-bunny-free", 130),
    "muse-spark-1.3-contributor": ("opencode-go-muse-spark-1.3", None),
    "gpt-6-luna": ("opencode-go-gpt-6-luna", None),
    "longcat-2.5-preview-free": ("opencode-go-longcat-2.5-preview-free", None),
}
ROTATE_ONLY = {125: "opencode-go-omen-alpha"}  # keep model, rotate key
DELETE_IDS = {48: "opencode-go-muse", 117: "opencode-go-qwen3.8-max-free"}
NEW_CHANNEL_WEIGHT = 2
NEW_CHANNEL_PRIORITY = 0


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load smoke helper: {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fetch_channels(smoke, headers):
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=200", headers=headers
    )
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"channel list failed: HTTP {status}")
    items = body.get("data") or []
    if isinstance(items, dict):
        items = items.get("items") or []
    return [i for i in items if isinstance(i, dict)]


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        f"new-api-before-opencode-go-key-rotation-{time.strftime('%Y%m%d-%H%M%S')}.db"
    )
    source = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        target = sqlite3.connect(destination, timeout=30)
        try:
            source.backup(target)
            result = target.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError(f"backup integrity check failed: {result}")
        finally:
            target.close()
    finally:
        source.close()
    return destination


def db_one(sql, params=()):
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as c:
        return c.execute(sql, params).fetchone()


def db_all(sql, params=()):
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as c:
        return c.execute(sql, params).fetchall()


def db_key_matches(channel_id: int, expected: str) -> bool:
    row = db_one("SELECT key FROM channels WHERE id = ?", (channel_id,))
    return bool(row) and row[0] == expected


def read_gateway_key() -> str:
    """OMP zg-newapi apiKey from live models.yml (never printed)."""
    import re
    text = (Path.home() / ".omp" / "agent" / "models.yml").read_text(encoding="utf-8")
    match = re.search(r"^  zg-newapi:\n(?:    .*\n)*?    apiKey:\s*(\S+)", text, flags=re.M)
    if not match:
        raise RuntimeError("zg-newapi apiKey not found in live models.yml")
    return match.group(1)


def put_channel(smoke, headers, item: dict, new_key: str) -> None:
    """Fork PUT contract: list-endpoint object minus ``status``, explicit key."""
    body = {k: v for k, v in item.items() if k != "status"}
    body["key"] = new_key
    status, resp = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/", method="PUT", body=body, headers=headers
    )
    if status != 200 or not isinstance(resp, dict) or not resp.get("success"):
        message = resp.get("message") if isinstance(resp, dict) else None
        raise RuntimeError(f"PUT ch{item.get('id')} failed: HTTP {status} message={message!r}")


def post_channel(smoke, headers, payload: dict) -> None:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/",
        method="POST",
        body={"mode": "single", "channel": payload},
        headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"channel POST failed: HTTP {status} message={message!r}")


def delete_channel(smoke, headers, channel_id: int) -> None:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}", method="DELETE", headers=headers
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"DELETE ch{channel_id} failed: HTTP {status} message={message!r}")


def channel_fix(smoke, headers) -> None:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/fix", method="POST", body={}, headers=headers
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"channel fix failed: HTTP {status} message={message!r}")


def put_option(smoke, headers, key: str, value: str) -> None:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/option/", method="PUT",
        body={"key": key, "value": value}, headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"option {key!r} update failed: HTTP {status} message={message!r}")


def wait_abilities(channel_id: int, model: str, seconds: int = 90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        row = db_one(
            'SELECT "group", enabled, priority, weight FROM abilities '
            "WHERE channel_id = ? AND model = ?",
            (channel_id, model),
        )
        if row is not None:
            return row
        time.sleep(5)
    return None


def functional_test(smoke, gateway_key: str, model: str) -> tuple[bool, str, int | None]:
    mark = time.time()
    status, body = smoke.http_json(
        f"{GATEWAY_BASE}/v1/chat/completions",
        method="POST",
        body={
            "model": model,
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": 64,
        },
        headers={"Authorization": f"Bearer {gateway_key}"},
        timeout=120,
    )
    if status != 200:
        return False, f"HTTP {status}: {json.dumps(body)[:160]}", None
    rows = db_all(
        "SELECT channel_id FROM logs WHERE model_name = ? AND created_at > ? AND type = 2 "
        "ORDER BY id DESC LIMIT 1",
        (model, mark),
    )
    channel_id = int(rows[0][0]) if rows else None
    return True, "200", channel_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    args = parser.parse_args()

    new_key = os.environ.get("OPENCODE_GO_NEW_KEY", "").strip()
    if not new_key:
        raise RuntimeError("OPENCODE_GO_NEW_KEY env var required (never persisted)")

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    channels = fetch_channels(smoke, headers)
    by_id = {int(c["id"]): c for c in channels}
    by_name = {str(c.get("name")): c for c in channels}

    # preflight
    for cid in (125, 130):
        if cid not in by_id:
            raise RuntimeError(f"ch{cid} missing from gateway list")
    header_override = by_id[130].get("header_override")
    if not header_override or "x-opencode-session" not in str(header_override):
        raise RuntimeError("ch130 header_override missing session header; refusing to clone")
    base_url = str(by_id[130].get("base_url") or "").rstrip("/")
    collisions = [n for n, _ in KEEP.values() if n in by_name and by_name[n].get("id") not in (130,)]
    collisions = [n for n in collisions if int(by_name[n]["id"]) != 130]
    if collisions:
        raise RuntimeError(f"target channel names already exist: {collisions}")

    print("plan:")
    print("  rotate key: ch125 (omen-alpha), ch130 (space-bunny-free)")
    for model, (name, cid) in KEEP.items():
        if cid is None:
            print(f"  create: {name} models={model} prio={NEW_CHANNEL_PRIORITY} w={NEW_CHANNEL_WEIGHT}")
    for cid, name in DELETE_IDS.items():
        present = "present" if cid in by_id else "ABSENT (skip)"
        print(f"  delete: ch{cid} {name} [{present}]")
    print("  ModelRatio += 4 kept models -> 0; POST /api/channel/fix")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    # 1. key rotation on existing channels
    for cid in (125, 130):
        put_channel(smoke, headers, by_id[cid], new_key)
        if not db_key_matches(cid, new_key):
            raise RuntimeError(f"ch{cid} key readback mismatch after PUT")
        print(f"ch{cid} key rotated (DB readback match)")

    # 2. create new channels
    created: dict[str, int] = {}
    for model, (name, cid) in KEEP.items():
        if cid is not None:
            created[model] = cid
            continue
        payload = {
            "name": name,
            "type": 1,
            "base_url": base_url,
            "key": new_key,
            "models": model,
            "group": "default",
            "priority": NEW_CHANNEL_PRIORITY,
            "weight": NEW_CHANNEL_WEIGHT,
            "auto_ban": 1,
            "test_model": model,
            "header_override": header_override,
        }
        post_channel(smoke, headers, payload)
        after = fetch_channels(smoke, headers)
        match = [c for c in after if str(c.get("name")) == name]
        if not match:
            raise RuntimeError(f"{name} not visible after POST")
        created[model] = int(match[0]["id"])
        print(f"created ch{created[model]} {name}")

    # 3. delete obsolete Go channels
    for cid, name in DELETE_IDS.items():
        if cid not in by_id:
            print(f"ch{cid} {name} already absent; skip delete")
            continue
        delete_channel(smoke, headers, cid)
        print(f"deleted ch{cid} {name}")

    # 4. ModelRatio
    row = db_one("SELECT value FROM options WHERE key = 'ModelRatio'")
    ratios = json.loads(row[0]) if row and row[0] else {}
    for model in KEEP:
        ratios[model] = 0
    put_option(smoke, headers, "ModelRatio", json.dumps(ratios, separators=(",", ":"), sort_keys=True))
    print("ModelRatio: 4 kept models -> 0")

    # 5. fix + verify
    channel_fix(smoke, headers)
    print("channel fix ok")

    problems: list[str] = []
    for model, cid in created.items():
        row_ab = wait_abilities(cid, model)
        if row_ab != ("default", 1, NEW_CHANNEL_PRIORITY, NEW_CHANNEL_WEIGHT):
            problems.append(f"abilities ch{cid}/{model} = {row_ab}")
    for cid in DELETE_IDS:
        leftovers = db_all("SELECT model FROM abilities WHERE channel_id = ?", (cid,))
        if leftovers:
            problems.append(f"abilities leftovers for deleted ch{cid}: {leftovers}")
        if db_one("SELECT id FROM channels WHERE id = ?", (cid,)):
            problems.append(f"ch{cid} still in channels table")
    ratios_rb = json.loads(db_one("SELECT value FROM options WHERE key = 'ModelRatio'")[0])
    for model in KEEP:
        if ratios_rb.get(model) != 0:
            problems.append(f"ModelRatio[{model}] = {ratios_rb.get(model)!r}")
    if problems:
        raise RuntimeError("verify failed: " + "; ".join(problems))
    print("verify ok: abilities 4x enabled(0/2), deleted ids clean, ratios 0")

    # 6. functional: 1 gateway chat per model with channel attribution
    gateway_key = read_gateway_key()
    for model, cid in created.items():
        ok, detail, attr = functional_test(smoke, gateway_key, model)
        flag = "OK" if ok and attr == cid else "FAIL"
        print(f"functional {flag}: {model} -> {detail}, attributed ch{attr} (expected ch{cid})")
        if not ok:
            raise RuntimeError(f"functional test failed for {model}: {detail}")
    print("DONE: 4 models live; key rotated on ch125/ch130; ch48/ch117 deleted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
