"""Park the five drained intern-discovery legs, or restore them.

Why these channels: ch140/141/142/143/146 are five keys of ONE upstream account
(base_url https://discovery-api.intern-ai.org.cn). The upstream pre-charges by request
size, so as the account drained it kept answering tiny probes with 200 while rejecting
real requests with 429 "quota exceeded". The largest successful prompt per day is the
shape of that drain: 2026-10-05 122158 -> 10-06 77751 -> 10-07 32946 -> 10-08 87 ->
10-11 17. Since 10-08 no request above ~90 prompt tokens has succeeded on any of the
five, i.e. they have served zero real traffic for four days while every probe-based
health check (NewAPI auto-test, Guardian full scan, gateway smoke) stayed green.

Why they must be parked by hand: auto_ban treats 429 as retryable, so NewAPI never
disables them and they hold the TOP enabled tier (priority 40) for glm-5.3, intern-s2,
deepseek-v4-flash-vision and qwen3-8-27b. Every real request therefore burns two dead
attempts before falling through, and the two names whose whole pool is these five legs
return the 429 to the client. The OMP consumers are config.yml `slow`/`plan`/`designer`/
`task`/`advisor` -> glm-5.3 (ch203 w3 / ch208 w1 stay behind them) and the `vision`
chain's second entry -> deepseek-v4-flash-vision.

Parked as status=2 with auto_ban=1 left alone: Guardian's recovery queue imports only
status==3 auto-bans, and a probe-based recovery would pass on a tiny prompt and
re-enable a drained account, which is the exact false-green that made this invisible.
Restore is deliberate, and the smoke gate's "probe-only drained legs" check re-flags
them within one run if they are restored before the upstream has headroom again.

Dry-run by default. --apply takes an online snapshot and verifies it first.
--restore puts them back.
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

CHANNELS = {
    140: "intern-discovery",
    141: "intern-discovery-k2",
    142: "intern-discovery-k3",
    143: "intern-discovery-k4",
    146: "intern-discovery-k5",
}
AFFECTED_MODELS = ["glm-5.3", "intern-s2", "deepseek-v4-flash-vision", "qwen3-8-27b"]
# These names exist only behind the five drained legs, so parking orphans them.
ORPHANED_MODELS = ["intern-s2", "deepseek-v4-flash-vision"]
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
    parser.add_argument("--restore", action="store_true", help="set the five legs back to enabled")
    parser.add_argument(
        "--allow-orphan-models",
        action="store_true",
        help=f"accept that {ORPHANED_MODELS} lose their last leg",
    )
    return parser.parse_args()


def key_fingerprints(db_path: Path) -> dict[int, tuple[int, str]]:
    """Length + digest per stored key, never the key itself.

    The management API redacts keys in this fork, so the only way to prove the status
    writes left the credentials alone is to compare them in the database.
    """
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        return {
            int(cid): (len(key or ""), hashlib.sha256(str(key or "").encode()).hexdigest()[:12])
            for cid, key in con.execute(
                "SELECT id, key FROM channels WHERE id IN ({})".format(
                    ",".join("?" * len(CHANNELS))
                ),
                tuple(CHANNELS),
            )
        }


def channel_states(smoke, headers: dict[str, str]) -> dict[int, dict]:
    out = {}
    for channel_id in CHANNELS:
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}", headers=headers
        )
        if status != 200 or not isinstance(body, dict):
            raise RuntimeError(f"cannot read ch{channel_id}: HTTP {status}")
        data = body.get("data") or {}
        out[channel_id] = {
            "status": data.get("status"),
            "name": data.get("name"),
            "priority": data.get("priority"),
            "weight": data.get("weight"),
            "auto_ban": data.get("auto_ban"),
        }
    return out


def ability_rows(db_path: Path, channel_id: int) -> list[tuple]:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        return con.execute(
            """SELECT model, "group", enabled, priority, weight FROM abilities
               WHERE channel_id=? ORDER BY model, "group" """,
            (channel_id,),
        ).fetchall()


def all_enabled_rows(db_path: Path) -> set[tuple]:
    """Every routable abilities row in the deployment, for the no-collateral-damage diff."""
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        return set(
            con.execute(
                """SELECT model, "group", channel_id, enabled, priority, weight FROM abilities"""
            ).fetchall()
        )


def routable_legs(db_path: Path) -> dict[str, list[str]]:
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as con:
        out = {}
        for model in AFFECTED_MODELS:
            rows = con.execute(
                """SELECT a.channel_id, c.name, a.priority, a.weight
                   FROM abilities a JOIN channels c ON c.id = a.channel_id
                   WHERE a.model=? AND a.enabled=1 AND c.status=1 AND a."group"='default'
                   ORDER BY a.priority DESC""",
                (model,),
            ).fetchall()
            out[model] = [f"ch{r[0]}({r[1]})p{r[2]}w{r[3]}" for r in rows]
        return out


def online_backup(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = HOME / f"new-api-before-park-intern-discovery-{stamp}.db"
    with closing(sqlite3.connect(db_path.as_posix(), timeout=30)) as src, closing(
        sqlite3.connect(target.as_posix())
    ) as dst:
        src.backup(dst)
    with closing(sqlite3.connect(target.as_posix())) as check:
        verdict = check.execute("PRAGMA integrity_check").fetchone()[0]
    if verdict != "ok":
        raise RuntimeError(f"backup failed integrity check: {verdict}")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()[:12]
    print(
        f"backup ok: {target.name} ({target.stat().st_size} bytes, integrity=ok, sha256={digest})"
    )
    return target


def set_status(smoke, headers: dict[str, str], channel_id: int, status: int) -> None:
    response, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}/status",
        method="POST",
        body={"status": status},
        headers=headers,
    )
    if response != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"status POST failed on ch{channel_id}: HTTP {response} body={body}")


def report_legs(label: str, legs: dict[str, list[str]]) -> None:
    print(f"routable {label}:")
    for model, chans in legs.items():
        print(f"   {model:<24} {chans or '(none)'}")


def main() -> int:
    args = parse_args()
    smoke = load("smoke", "newapi-local-smoke.py")
    db_path = Path(smoke.NEWAPI_DB).resolve()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    want = 1 if args.restore else 2
    verb = "RESTORE" if args.restore else "PARK"

    states = channel_states(smoke, headers)
    drift = {cid: st["name"] for cid, st in states.items() if st["name"] != CHANNELS[cid]}
    if drift:
        raise RuntimeError(f"channel identity drift, refusing to write: {drift}")
    keys_before = key_fingerprints(db_path)
    empty = [cid for cid, (length, _) in keys_before.items() if not length]
    if empty:
        raise RuntimeError(f"legs without a stored key: {empty}")
    global_before = all_enabled_rows(db_path)
    for cid, state in sorted(states.items()):
        print(
            f"ch{cid} {state['name']}: status={state['status']} prio={state['priority']} "
            f"w={state['weight']} auto_ban={state['auto_ban']} "
            f"key len={keys_before[cid][0]} sha256={keys_before[cid][1]}"
        )
    report_legs("before", routable_legs(db_path))

    pending = [cid for cid, state in states.items() if state["status"] != want]
    if not pending:
        print(f"nothing to do: all five legs already status={want}")
        return 0

    if not args.restore:
        after = {
            model: [leg for leg in legs if not any(f"ch{cid}(" in leg for cid in CHANNELS)]
            for model, legs in routable_legs(db_path).items()
        }
        orphans = sorted(m for m, legs in after.items() if not legs)
        if orphans and not args.allow_orphan_models:
            print(f"BLOCKED: parking leaves {orphans} with zero legs.")
            print(
                "         those names exist only behind these five channels, so the OMP "
                "vision\n         chain entry and the intern-s2 monitor will fail fast instead "
                "of 429ing.\n         Re-run with --allow-orphan-models to accept that."
            )
            return 2
        if orphans:
            print(f"accepted orphans after park: {orphans}")

    if not args.apply:
        print(
            f"dry-run: would POST /api/channel/{cid}/status {{'status': {want}}} for "
            f"ch{pending} ({verb})"
        )
        return 0

    online_backup(db_path)
    try:
        for channel_id in pending:
            set_status(smoke, headers, channel_id, want)
            print(f"ch{channel_id} {verb} accepted")
    except RuntimeError as error:
        print(f"write failed part-way: {error}")
        print(f"re-run this script to converge the remaining legs; snapshot above")
        raise
    print(f"waiting {CACHE_SYNC_SECONDS}s for the channel cache and abilities")
    time.sleep(CACHE_SYNC_SECONDS)

    after_states = channel_states(smoke, headers)
    stuck = {cid: st["status"] for cid, st in after_states.items() if st["status"] != want}
    if stuck:
        raise RuntimeError(f"status did not stick: {stuck}")
    keys_after = key_fingerprints(db_path)
    if keys_after != keys_before:
        print(f"   WARN stored keys changed: {keys_before} -> {keys_after}")
    else:
        print("   stored keys unchanged (len + sha256 per leg)")
    global_after = all_enabled_rows(db_path)
    foreign = sorted(
        row
        for row in (global_before ^ global_after)
        if row[2] not in CHANNELS
    )
    if foreign:
        raise RuntimeError(f"abilities rows of OTHER channels changed (collateral damage): {foreign}")
    for channel_id in sorted(CHANNELS):
        print(f"   ch{channel_id} abilities: {ability_rows(db_path, channel_id)}")
    report_legs("after", routable_legs(db_path))
    print(f"\nrollback: python {HERE.name} --restore --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
