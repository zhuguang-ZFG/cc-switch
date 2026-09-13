# Enable ch128 (sharedchat-codex-astra) and ch86 (agentrouter-claude) with
# abilities re-sync, per the ch127/hashneuron PUT contract.
#
# Why status FIRST, then PUT: the fork's abilities sync derives want_enabled
# from the channel's current status (add_hashneuron_channel.py sync_flow
# line ~530: want_enabled = 1 if status == 1 else 0). PUT while status=2
# would re-disable the very abilities we are restoring.
#
# Dry-run by default; --apply executes. Keys are read from the local DB
# inside this process and never printed.
import argparse
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from add_hashneuron_channel import (  # noqa: E402
    CACHE_SYNC_SECONDS,
    list_channels,
    load_smoke,
    online_backup,
    put_channel_retry,
    management_probe,
)

DB_PATH = Path("C:/Users/zhugu/.new-api-local/new-api.db")
TARGETS = (
    {"id": 128, "label": "sharedchat-codex-astra", "probe_model": "gpt-6-astra"},
    {"id": 86, "label": "agentrouter-claude", "probe_model": "claude-opus-5"},
)


def read_real_key(channel_id: int) -> str:
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)) as c:
        return c.execute("SELECT key FROM channels WHERE id=?", (channel_id,)).fetchone()[0]


def read_abilities(channel_id: int) -> dict:
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)) as c:
        return {r[0]: r[1] for r in c.execute(
            "SELECT model, enabled FROM abilities WHERE channel_id=?", (channel_id,)
        ).fetchall()}


def enable_one(smoke, headers: dict, target: dict, apply: bool) -> None:
    cid = target["id"]
    items = {int(i["id"]): i for i in list_channels(smoke, headers)}
    if cid not in items:
        raise RuntimeError(f"channel {cid} not found")
    ch = items[cid]
    abilities = read_abilities(cid)
    key = read_real_key(cid)
    print(f"ch{cid} status={ch.get('status')} abilities={abilities} "
          f"models={ch.get('models')} p{ch.get('priority')}/w{ch.get('weight')}")

    if ch.get("status") == 1 and all(v == 1 for v in abilities.values()):
        print(f"ch{cid} already enabled with abilities on; nothing to do")
        return
    if not apply:
        print(f"dry-run: would enable ch{cid} (status->1) then PUT to sync abilities")
        return

    # 1. enable first — fork abilities sync derives want_enabled from status
    status_code, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{cid}/status",
        method="POST", body={"status": 1}, headers=headers, timeout=30,
    )
    if not (status_code == 200 and isinstance(body, dict) and body.get("success")):
        raise RuntimeError(f"ch{cid} enable failed: HTTP {status_code} {str(body)[:160]}")
    print(f"ch{cid} enabled")

    # 2. full-object PUT (drop status, set real key) -> fork syncs abilities
    payload = {
        "id": cid,
        "name": ch["name"],
        "type": ch["type"],
        "key": key,  # GET masks the key to empty string; set the real one
        "base_url": ch["base_url"],
        "models": ch["models"],
        "group": ch.get("group") or "default",
        "test_model": ch.get("test_model") or target["probe_model"],
        "model_mapping": ch.get("model_mapping") or "{}",
        "priority": ch.get("priority"),
        "weight": ch.get("weight"),
        "auto_ban": ch.get("auto_ban", 1),
        # no status field: keep the status we just set
    }
    put_channel_retry(smoke, headers, payload, f"ch{cid}-abilities-sync")

    print(f"waiting {CACHE_SYNC_SECONDS}s for channel cache sync")
    time.sleep(CACHE_SYNC_SECONDS)

    # 3. strict readback
    abilities = read_abilities(cid)
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)) as c:
        status_now = c.execute("SELECT status FROM channels WHERE id=?", (cid,)).fetchone()[0]
    if status_now != 1 or not all(v == 1 for v in abilities.values()):
        raise RuntimeError(
            f"ch{cid} readback mismatch: status={status_now} abilities={abilities}"
        )
    print(f"ch{cid} readback ok: status=1 abilities={abilities}")

    # 4. management probe — reported, never a gate (quota windows /
    #    codex-access-restricted are expected shapes for these channels)
    try:
        result = management_probe(smoke, headers, cid, target["probe_model"])
    except RuntimeError as error:
        result = f"error: {error}"
    print(f"ch{cid} probe {target['probe_model']}: {result}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": "Bearer " + token, "New-Api-User": str(user_id)}

    if args.apply:
        backup = online_backup(DB_PATH)
        print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes)")
        time.sleep(2)  # let the backup's read lock release

    for target in TARGETS:
        enable_one(smoke, headers, target, args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
