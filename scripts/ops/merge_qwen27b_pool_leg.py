"""Aggregate ch173 (muyuan-gongyi) into the shared `qwen3-8-27b` pool.

2026-10-11 aggregation census: ch173 declares the upstream name `qwen3.8-27b` and owns the
only abilities row for it (0 demand), while 8 channels serve the local name `qwen3-8-27b`.
ch140-143 already prove the intended shape — they map local `qwen3-8-27b` onto upstream
`qwen3.8-27b`. This script gives ch173 the same alias so its leg joins the real pool at its
existing bottom tier (priority 20), instead of sitting behind a name nobody requests.

Danger this script exists to control: PUT /api/channel/ rewrites every abilities row the
channel owns, and ch173 also carries `qwen3.8-flash-next` (629 real successes/24h). A lost
or re-tiered row there is a production outage, so the post-write check diffs ALL of ch173's
rows against the pre-write snapshot and fails loudly on any drift.

Default is read-only. --apply writes (after an online backup); --teardown restores the
captured models/model_mapping. Keys are read from the local SSOT for the PUT and never
printed; only length and a sha256 prefix are reported.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
CHANNEL_ID = 173
POOL_MODEL = "qwen3-8-27b"      # the local name clients actually request
UPSTREAM_MODEL = "qwen3.8-27b"  # the name muyuan serves
GROUP = "default"
CACHE_SYNC_SECONDS = 75
SNAPSHOT_GLOB = "bak-ch173-pre-qwen27b-merge-*.json"


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
                        help="restore the models/model_mapping captured by --apply")
    return parser.parse_args()


def redact(key: str) -> str:
    return f"len={len(key)} sha12={hashlib.sha256(key.encode()).hexdigest()[:12]}"


def db_channel(db_path: Path, channel_id: int) -> dict:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"channel {channel_id} missing from local SSOT")
    return dict(row)


def channel_abilities(db_path: Path, channel_id: int) -> list[tuple]:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        return sorted(con.execute(
            'SELECT model, "group", enabled, priority, weight FROM abilities '
            'WHERE channel_id = ? ORDER BY model, "group"', (channel_id,)).fetchall())


def pool_legs(db_path: Path, model: str) -> list[tuple]:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        return sorted(con.execute(
            'SELECT a.channel_id, c.name, a.enabled, a.priority, a.weight FROM abilities a '
            'JOIN channels c ON c.id = a.channel_id WHERE a.model = ? AND a."group" = ?',
            (model, GROUP)).fetchall())


def ratio_registered(db_path: Path, model: str) -> bool:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        row = con.execute("SELECT value FROM options WHERE key = 'ModelRatio'").fetchone()
    ratios = json.loads(row[0]) if row and row[0] else {}
    return model in ratios


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    destination = backup_dir / f"new-api-before-qwen27b-merge-{time.strftime('%Y%m%d-%H%M%S')}.db"
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


def management_probe(smoke, headers: dict[str, str], model: str) -> bool:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/test/{CHANNEL_ID}?model={model}",
        headers=headers, timeout=170)
    ok = status == 200 and isinstance(body, dict) and bool(body.get("success"))
    detail = "" if ok else json.dumps(body, ensure_ascii=False)[:180]
    print(f"  management probe {model:<14} {'ok' if ok else 'FAIL'} "
          f"time={body.get('time') if isinstance(body, dict) else '-'} {detail}")
    return ok


def put_channel(smoke, headers: dict[str, str], payload: dict) -> None:
    status, body = smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/", method="PUT",
                                   body=payload, headers=headers, timeout=60)
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"channel PUT failed: HTTP {status} {str(body)[:200]}")


def build_payload(live: dict, db_row: dict, models: str, mapping: str) -> dict:
    """PUT the admin-view object, hydrating the key from the local SSOT.

    Mirrors Guardian's update path: the management API masks the key, NewAPI rejects a
    status field on update, and a channel whose key looks masked must never be written.
    """
    key = db_row.get("key")
    key = key.decode() if isinstance(key, bytes) else (key or "")
    if not key.strip() or "*" in key:
        raise RuntimeError(f"refusing PUT with masked/absent key: {redact(key)}")
    if db_row.get("name") != live.get("name"):
        raise RuntimeError("local SSOT channel identity does not match the admin view")
    payload = {k: v for k, v in live.items() if k not in ("status", "key")}
    if payload.get("id") != CHANNEL_ID:
        raise RuntimeError(f"admin view id {payload.get('id')!r} is not {CHANNEL_ID}; refusing PUT")
    payload["key"] = key
    payload["models"] = models
    payload["model_mapping"] = mapping
    return payload


def assert_row_set(pre: list[tuple], post: list[tuple], added: list[tuple]) -> None:
    """Every pre-write abilities row must survive untouched; only `added` may appear."""
    pre_set, post_set = set(pre), set(post)
    lost = sorted(pre_set - post_set)
    unexpected = sorted((post_set - pre_set) - set(added))
    if lost:
        raise RuntimeError(f"abilities rows LOST by the PUT (production regression): {lost}")
    if unexpected:
        raise RuntimeError(f"abilities rows appeared that this script did not intend: {unexpected}")


def main() -> int:
    args = parse_args()
    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    db_row = db_channel(db_path, CHANNEL_ID)
    status_code, live_body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{CHANNEL_ID}", headers=headers)
    if status_code != 200 or not isinstance(live_body, dict) or not live_body.get("data"):
        raise RuntimeError(f"admin GET channel {CHANNEL_ID} failed: HTTP {status_code}")
    live = live_body["data"]
    declared = [m.strip() for m in str(live.get("models") or "").split(",") if m.strip()]
    mapping_raw = live.get("model_mapping") or ""
    try:
        mapping = json.loads(mapping_raw) if mapping_raw else {}
    except ValueError:
        raise RuntimeError(f"model_mapping is not JSON: {mapping_raw[:80]!r}") from None

    pre_rows = channel_abilities(db_path, CHANNEL_ID)
    pool_pre = pool_legs(db_path, POOL_MODEL)
    print(f"ch{CHANNEL_ID} {live.get('name')} status={live.get('status')} "
          f"priority={live.get('priority')} weight={live.get('weight')} group={live.get('group')} "
          f"auto_ban={live.get('auto_ban')} key[{redact(str(db_row.get('key') or ''))}]")
    print(f"  declares {len(declared)} models; owns {len(pre_rows)} abilities rows; "
          f"model_mapping={json.dumps(mapping, ensure_ascii=False)}")
    print(f"  channel_info={live.get('channel_info') or db_row.get('channel_info')} "
          f"(a PUT on a multi-key channel can flatten key state, so it is disclosed here)")
    print(f"  pool {POOL_MODEL}/{GROUP}: {len(pool_pre)} rows, "
          f"enabled={sorted({r[0] for r in pool_pre if r[2] == 1})}")

    if args.teardown:
        snapshots = sorted(glob.glob(str(Path(db_path).parent / SNAPSHOT_GLOB)))
        if not snapshots:
            raise RuntimeError(f"no {SNAPSHOT_GLOB} snapshot to roll back to")
        snap = json.loads(Path(snapshots[-1]).read_text(encoding="utf-8"))
        target_models, target_mapping = snap["models"], snap["model_mapping"]
        print(f"teardown to snapshot {os.path.basename(snapshots[-1])}")
    else:
        if UPSTREAM_MODEL not in declared:
            raise RuntimeError(f"ch{CHANNEL_ID} no longer declares {UPSTREAM_MODEL}; premise false")
        if live.get("status") != 1:
            raise RuntimeError(f"ch{CHANNEL_ID} is not enabled (status={live.get('status')}); "
                               "a new abilities row would be created disabled and the merge would be a no-op")
        if pool_pre:
            enabled_pool = sorted({r[0] for r in pool_pre if r[2] == 1})
            if not enabled_pool:
                raise RuntimeError(f"pool {POOL_MODEL} has no enabled leg; nothing to aggregate into")
        else:
            raise RuntimeError(f"pool {POOL_MODEL} has no abilities rows at all; premise false")
        if not ratio_registered(db_path, POOL_MODEL):
            # Pre-existing condition, not a consequence of this write: the pool already
            # serves this name today (ch88/ch124 carry its traffic), so adding a leg cannot
            # change what it bills. Disclosed rather than refused; registering the ratio is a
            # deliberate pricing decision that belongs to the billing task, not here.
            print(f"  WARNING {POOL_MODEL} is absent from ModelRatio -> the pool already bills "
                  f"it at the implied ratio; this script writes no option")
        target_models = ",".join(dict.fromkeys(declared + [POOL_MODEL]))
        target_mapping = json.dumps({**mapping, POOL_MODEL: UPSTREAM_MODEL}, ensure_ascii=False)
        if POOL_MODEL in declared and POOL_MODEL in mapping:
            print(f"  already aggregated ({POOL_MODEL} -> {UPSTREAM_MODEL}); nothing to write")
            return 0

    payload = build_payload(live, db_row, target_models, target_mapping)
    print(f"  intended models={target_models!r}")
    print(f"  intended model_mapping={target_mapping!r}")
    if not (args.apply or args.teardown):
        print("DRY RUN: no write performed. Re-run with --apply.")
        return 0

    print("  health precondition: upstream must answer for the model being aliased")
    if not (args.teardown or management_probe(smoke, headers, UPSTREAM_MODEL)):
        raise RuntimeError(f"management probe for {UPSTREAM_MODEL} failed; refusing to pool an "
                           "unproven leg")

    backup = online_backup(db_path)
    size = backup.stat().st_size
    print(f"  backup {backup} bytes={size} integrity=ok")
    snapshot = Path(db_path).parent / f"bak-ch173-pre-qwen27b-merge-{time.strftime('%Y%m%d-%H%M%S')}.json"
    snapshot.write_text(json.dumps({
        "channel_id": CHANNEL_ID, "name": live.get("name"),
        "models": live.get("models") or "", "model_mapping": mapping_raw,
        "priority": live.get("priority"), "weight": live.get("weight"),
        "status": live.get("status"), "group": live.get("group"),
        "auto_ban": live.get("auto_ban"),
        "key": redact(str(db_row.get("key") or "")),
        "abilities_rows": [list(r) for r in pre_rows],
        "backup": str(backup),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  rollback artifact {snapshot.name} (key stored redacted)")

    put_channel(smoke, headers, payload)
    print(f"  PUT accepted; waiting {CACHE_SYNC_SECONDS}s for the abilities cache to resync")
    time.sleep(CACHE_SYNC_SECONDS)

    post_row = db_channel(db_path, CHANNEL_ID)
    post_key = str(post_row.get("key") or "")
    if redact(post_key) != redact(str(db_row.get("key") or "")):
        raise RuntimeError("channel key fingerprint changed during the PUT")
    post_rows = channel_abilities(db_path, CHANNEL_ID)
    alias = (POOL_MODEL, GROUP)
    added = {tuple(r) for r in post_rows if r[0] == POOL_MODEL}
    if args.teardown:
        assert_row_set(pre_rows, post_rows, [])
        if any(r[0] == POOL_MODEL for r in post_rows):
            raise RuntimeError(f"teardown left {POOL_MODEL} abilities rows: "
                               f"{[r for r in post_rows if r[0] == POOL_MODEL]}")
        print(f"  teardown verified: {len(post_rows)} rows, {POOL_MODEL} gone, key unchanged")
    else:
        assert_row_set(pre_rows, post_rows, added)
        legs = pool_legs(db_path, POOL_MODEL)
        mine = [r for r in legs if r[0] == CHANNEL_ID]
        if not mine or mine[0][2] != 1:
            raise RuntimeError(f"ch{CHANNEL_ID} did not enter the {POOL_MODEL} pool: {mine}")
        print(f"  added rows: {sorted(added)}")
        print(f"  pool {POOL_MODEL}/{GROUP} now: {len(legs)} rows, "
              f"enabled={sorted({r[0] for r in legs if r[2] == 1})}")
        print("  end-to-end: alias must route through this channel after resync")
        if not management_probe(smoke, headers, POOL_MODEL):
            raise RuntimeError(f"alias {POOL_MODEL} did not answer via ch{CHANNEL_ID}")
    print(f"total={len(post_rows)} ch{CHANNEL_ID} rows after write; all pre-existing rows intact")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 - ops CLI must report, not traceback
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
