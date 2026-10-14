#!/usr/bin/env python3
"""Rotate ch216 stepfun-official onto the 2026-10-14 shared official key and
restore the documented p0/w1 posture.

Why: the 2026-10-12 p10 bump was an incident posture ("ch205 plan credit
exhausted ... weighted random within p0 sent ~5/6 of step traffic into the dead
ch205"). ch205 is being restored on a fresh plan key (100% quota), so the bump's
premise is gone: the plan leg goes back to primary and this official leg returns
to its documented same-tier last resort p0/w1. Flip intent is recorded here, not
assumed elsewhere.

Key source: ~/.new-api-local/step-plan-keys-1014.txt line 2 (value never
printed; only len + sha256 prefix are reported). Default is a dry run; --apply
runs direct gate -> online backup -> PUT rotation -> enable (status 1, before
fix so abilities land enabled=1) -> channel/fix -> abilities read-back -> admin
channel test -> channel-cache wait -> gateway relay
-> log-attribution check. ch224 (step-plan, p0/w5) is already live and shares the
p0 tier with ch216 (p0/w1), so weighted routing may land on either leg; the
check accepts {224, 216} and reports which one won.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEY_FILE = Path.home() / ".new-api-local" / "step-plan-keys-1014.txt"
CACHE_SYNC_SECONDS = 75


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


leg = load(HERE / "add_stepfun_official_leg.py", "stepfun_official_leg")


def fingerprint(key: str) -> str:
    return f"len={len(key)} sha12={hashlib.sha256(key.encode()).hexdigest()[:12]}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply (default: dry-run)")
    parser.add_argument("--channel-id", type=int, default=216)
    args = parser.parse_args()

    keys = [l.strip() for l in KEY_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    if len(keys) < 2:
        raise RuntimeError(f"{KEY_FILE.name} must hold the two 2026-10-14 keys")
    key = keys[1]
    new_id = args.channel_id
    smoke = leg.load_smoke()

    print(f"plan: rotate ch{new_id} {leg.CHANNEL_NAME} key {fingerprint(key)}; "
          f"posture p{leg.PRIORITY}/w{leg.WEIGHT} -> p0/w1; models {len(leg.MODELS)}")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    leg.direct_gate(key)
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}
    backup = leg.online_backup(leg.DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes)")

    payload = {
        "id": new_id,
        "name": leg.CHANNEL_NAME,
        "type": leg.CHANNEL_TYPE,
        "base_url": leg.BASE_URL,
        "key": key,
        "models": ",".join(leg.MODELS),
        "group": "default",
        "priority": 0,
        "weight": 1,
        "auto_ban": leg.AUTO_BAN,
        "test_model": leg.TEST_MODEL,
    }
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/", method="PUT", body=payload, headers=headers
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"channel PUT failed: HTTP {status} {str(body)[:200]}")
    print(f"PUT ok: ch{new_id} key rotated; posture p0/w1")

    # Enable FIRST: the key is already rotated, so there is no dead-key auto-ban
    # risk, and abilities for a disabled channel come back enabled=0 which would
    # fail the wait_abilities assertion below. status POST flips the channel row
    # and its abilities.enabled; fix can run either side of it.
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{new_id}/status", method="POST",
        body={"status": 1}, headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"status POST failed: HTTP {status} {str(body)[:200]}")
    print(f"ch{new_id} enabled")

    after = leg.fetch_channels(smoke, headers)
    match = [c for c in after if int(c.get("id") or 0) == new_id]
    if not match:
        raise RuntimeError(f"ch{new_id} not visible after PUT")
    row = match[0]
    stored = set(str(row.get("models") or "").split(",")) - {""}
    if stored != set(leg.MODELS):
        raise RuntimeError(f"read-back models mismatch: {sorted(stored ^ set(leg.MODELS))}")
    got_priority = int(row.get("priority") if row.get("priority") is not None else -1)
    got_weight = int(row.get("weight") if row.get("weight") is not None else -1)
    if got_priority != 0 or got_weight != 1:
        raise RuntimeError(f"read-back posture mismatch: p{got_priority}/w{got_weight}")
    print(f"read-back ok: models {len(stored)}, p{got_priority}/w{got_weight}, status={row.get('status')}")

    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/fix", method="POST", body={}, headers=headers
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"channel fix failed: HTTP {status} {str(body)[:200]}")
    for model in leg.MODELS:
        row_ab = leg.wait_abilities(new_id, model)
        if not row_ab or tuple(row_ab) != ("default", 1, 0, 1):
            raise RuntimeError(f"abilities ch{new_id}/{model} = {row_ab} (absent or wrong shape after 90s)")
    print(f"verify ok: abilities ch{new_id} x{len(leg.MODELS)} (default,1,0,1)")

    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/test/{new_id}?model={leg.TEST_MODEL}",
        headers=headers, timeout=120,
    )
    test_ok = isinstance(body, dict) and bool(body.get("success"))
    print(f"admin test: HTTP {status} success={test_ok} "
          f"time={body.get('time') if isinstance(body, dict) else None} model={leg.TEST_MODEL}")
    if not test_ok:
        raise RuntimeError(f"admin channel test failed: {str(body)[:200]}")

    print(f"waiting {CACHE_SYNC_SECONDS}s for the channel cache", flush=True)
    time.sleep(CACHE_SYNC_SECONDS)

    gateway_key = leg.read_gateway_key()
    status, usage, attr = leg.gateway_chat(smoke, gateway_key, leg.TEST_MODEL)
    print(f"relay {leg.TEST_MODEL}: HTTP {status} usage={usage} attributed ch{attr}")
    if status != 200:
        raise RuntimeError(f"gateway relay failed: HTTP {status}")
    if attr not in (new_id, 224):
        raise RuntimeError(
            f"relay attributed ch{attr}; expected ch{new_id} or ch224 — routing defect"
        )
    if attr == 224:
        print(f"posture ok: relay landed on ch224 (p0/w5 plan leg, higher weight); "
              f"ch{new_id} idle as designed")
    print(f"DONE: ch{new_id} live on the shared key at p0/w1. Recovery snapshot: {backup.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
