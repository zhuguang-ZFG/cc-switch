#!/usr/bin/env python3
"""Expand the intern-discovery key pool with new same-upstream keys (2026-10-03).

Background
----------
The official InternAI source (``https://discovery-api.intern-ai.org.cn``) is
already onboarded as a single-key-per-channel pool: ch140 ``intern-discovery``
+ ch141/142/143 ``-k2/-k3/-k4`` (status=1, prio=40, weight=1, models
``glm-5.3, intern-s2, deepseek-v4-flash-vision, qwen3-8-27b``). The user
supplied two NEW keys (hash-verified absent from all pool channels) to add as
``-k5``/``-k6`` following the same pattern (fork lesson: PUT cannot add keys
to an existing channel, so pools grow as separate single-key channels).

POOL_MODELS here is ``glm-5.3, intern-s2, deepseek-v4-flash-vision`` — the
pool's catalog-verified subset. The 4th pool model ``qwen3-8-27b`` does NOT
exist in the upstream catalog (upstream spells it ``qwen3.8-27b``); that
pre-existing discrepancy is documented in the runbook and left untouched.

Hard gates, in order (a pool-level probe can NOT prove new keys work — an
attribution hit on an old pool member would mask dead new keys):
1. idempotency: sha256 of each env key vs every key on same-base channels
   (hashes only, never printed); keys already in pool are skipped;
2. per-key direct upstream chat probe — a failing key is EXCLUDED from
   creation (all failing -> zero writes, raise);
3. channel creation + abilities shape verification per new channel;
4. per-channel admin test GET /api/channel/test/{id} (exercises the exact
   key+base+test_model configuration) — failure raises, channel left in
   place for forensics;
5. gateway pool chat with attribution retry = rotation-health note only.

Usage: ``INTERN_AI_KEY=sk-... [INTERN_AI_KEY_2=sk-...] python3 add_intern_discovery_pool_keys.py [--apply]``
Keys come from env vars only; never printed, logged, or written to the repo.
Dry-run by default (no key required).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
DB_PATH = Path.home() / ".new-api-local" / "new-api.db"
GATEWAY_BASE = "http://127.0.0.1:3002"

# base_url WITHOUT /v1: NewAPI auto-appends /v1/chat/completions
BASE_URL = "https://discovery-api.intern-ai.org.cn"
POOL_NAME_PREFIX = "intern-discovery"
POOL_MODELS = ["glm-5.3", "intern-s2", "deepseek-v4-flash-vision"]
PRIORITY = 40
WEIGHT = 1
FUNCTIONAL_MODEL = "intern-s2"


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load smoke helper: {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def db_one(sql, params=()):
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as c:
        return c.execute(sql, params).fetchone()


def db_all(sql, params=()):
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as c:
        return c.execute(sql, params).fetchall()


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        f"new-api-before-intern-pool-keys-{time.strftime('%Y%m%d-%H%M%S')}.db"
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


def read_gateway_key() -> str:
    """OMP zg-newapi apiKey from live models.yml (never printed)."""
    text = (Path.home() / ".omp" / "agent" / "models.yml").read_text(encoding="utf-8")
    match = re.search(r"^  zg-newapi:\n(?:    .*\n)*?    apiKey:\s*(\S+)", text, flags=re.M)
    if not match:
        raise RuntimeError("zg-newapi apiKey not found in live models.yml")
    return match.group(1)


def wait_abilities(channel_id: int, model: str, seconds: int = 90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        row = db_one(
            "SELECT [group], enabled, priority, weight FROM abilities WHERE channel_id = ? AND model = ?",
            (channel_id, model),
        )
        if row:
            return row
        time.sleep(2)
    return None


def key_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def pool_key_hashes() -> set:
    """sha256 of every key part on channels sharing the pool base_url (never printed)."""
    rows = db_all("SELECT key FROM channels WHERE base_url = ?", (BASE_URL,))
    hashes = set()
    for (raw,) in rows:
        for part in str(raw).split("\n"):
            part = part.strip()
            if part:
                hashes.add(key_hash(part))
    return hashes


def next_pool_names(existing_names, count):
    taken = set(existing_names)
    names = []
    index = 2  # ch140 is the bare name; -k2/-k3/-k4 follow
    while len(names) < count:
        candidate = f"{POOL_NAME_PREFIX}-k{index}"
        if candidate not in taken:
            names.append(candidate)
            taken.add(candidate)
        index += 1
    return names


def upstream_chat_probe(key: str):
    """Direct upstream chat with one key. Returns (ok, note). Key never printed."""
    req = urllib.request.Request(
        f"{BASE_URL}/v1/chat/completions",
        data=json.dumps({
            "model": FUNCTIONAL_MODEL,
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": 512,
        }).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # network/timeout/decode
        return False, f"{type(exc).__name__}: {exc}"
    usage = (body.get("usage") or {}) if isinstance(body, dict) else {}
    if not usage.get("completion_tokens"):
        return False, f"200 but no completion usage: {json.dumps(body)[:160]}"
    return True, f"usage={usage.get('prompt_tokens')}/{usage.get('completion_tokens')}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    args = parser.parse_args()

    keys = [k for k in (
        os.environ.get("INTERN_AI_KEY", "").strip(),
        os.environ.get("INTERN_AI_KEY_2", "").strip(),
    ) if k]

    print("plan:")
    print(f"  expand pool {POOL_NAME_PREFIX}-kN (single-key channels, prio={PRIORITY} w={WEIGHT})")
    print(f"  base={BASE_URL} (no /v1; auto-appended); models={','.join(POOL_MODELS)}")
    print("  gates: sha256 idempotency -> per-key upstream probe -> create+abilities")
    print("         -> per-channel admin test -> gateway rotation note")
    print("  ModelRatio: UNSET (pricing unknown — open item)")

    if not args.apply:
        print("dry-run: no changes made")
        return 0
    if not keys:
        raise RuntimeError("INTERN_AI_KEY env var required (INTERN_AI_KEY_2 optional)")

    # gate 1: idempotency
    new_keys = []
    known = pool_key_hashes()
    for k in keys:
        if key_hash(k) in known:
            print("skip: one provided key already present in pool (hash match)")
        else:
            new_keys.append(k)
    print(f"keys loaded: {len(keys)}; new to pool: {len(new_keys)}")
    if not new_keys:
        print("DONE: nothing to add (all provided keys already in pool)")
        return 0

    # apply-time catalog discovery (catalog printed, key never)
    req = urllib.request.Request(
        f"{BASE_URL}/v1/models", headers={"Authorization": f"Bearer {new_keys[0]}"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        catalog = json.loads(resp.read().decode("utf-8"))
    ids = [m.get("id") for m in catalog.get("data", []) if isinstance(m, dict)]
    print(f"upstream catalog ({len(ids)}): {ids}")
    missing = [m for m in POOL_MODELS if m not in ids]
    if missing:
        raise RuntimeError(f"POOL_MODELS missing from upstream catalog: {missing}")

    # gate 2: per-key direct upstream chat probe — failures excluded, never created
    good_keys = []
    for k in new_keys:
        ok, note = upstream_chat_probe(k)
        print(f"upstream probe: one key -> {'OK ' if ok else 'FAIL '}{note}")
        if ok:
            good_keys.append(k)
    if not good_keys:
        raise RuntimeError("no provided key passed the upstream chat probe; nothing created")
    if len(good_keys) < len(new_keys):
        print(f"WARN: {len(new_keys) - len(good_keys)} key(s) failed upstream probe, excluded")

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    channels = fetch_channels(smoke, headers)
    existing_names = {str(c.get("name")) for c in channels}
    names = next_pool_names(existing_names, len(good_keys))
    print(f"new channel names: {names}")

    backup = online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    # gate 3: creation + abilities shape
    created = []
    for name, key in zip(names, good_keys):
        payload = {
            "name": name,
            "type": 1,
            "base_url": BASE_URL,
            "key": key,
            "models": ",".join(POOL_MODELS),
            "group": "default",
            "priority": PRIORITY,
            "weight": WEIGHT,
            "auto_ban": 1,
            "test_model": POOL_MODELS[0],
        }
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/",
            method="POST",
            body={"mode": "single", "channel": payload},
            headers=headers,
        )
        if status != 200 or not isinstance(body, dict) or not body.get("success"):
            message = body.get("message") if isinstance(body, dict) else None
            raise RuntimeError(f"channel POST failed for {name}: HTTP {status} message={message!r}")
        after = fetch_channels(smoke, headers)
        match = [c for c in after if str(c.get("name")) == name]
        if not match:
            raise RuntimeError(f"{name} not visible after POST")
        new_id = int(match[0]["id"])
        created.append((new_id, name))
        print(f"created ch{new_id} {name}")

    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/fix", method="POST", body={}, headers=headers
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"channel fix failed: HTTP {status} message={message!r}")

    for new_id, name in created:
        for m in POOL_MODELS:
            row_ab = wait_abilities(new_id, m)
            if not row_ab or tuple(row_ab) != ("default", 1, PRIORITY, WEIGHT):
                raise RuntimeError(f"abilities ch{new_id}/{m} = {row_ab} (absent or wrong shape after 90s)")
        print(f"verify ok: abilities ch{new_id} {name} x{len(POOL_MODELS)} (default,1,{PRIORITY},{WEIGHT})")

    # gate 4: per-channel admin test (exercises exact key+base+test_model config)
    for new_id, name in created:
        t_status, t_body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/test/{new_id}?model={FUNCTIONAL_MODEL}",
            headers=headers, timeout=180,
        )
        ok = t_status == 200 and isinstance(t_body, dict) and t_body.get("success")
        if not ok:
            raise RuntimeError(
                f"admin channel test failed for ch{new_id} {name}: HTTP {t_status} "
                f"{str(t_body)[:200]} (channel left in place for forensics)"
            )
        print(f"admin test ok: ch{new_id} {name}")

    # gate 5: gateway pool chat — rotation-health note only (NOT per-key proof)
    gateway_key = read_gateway_key()
    usage = {}
    attr = None
    for attempt in range(6):
        mark = time.time()
        status, body = smoke.http_json(
            f"{GATEWAY_BASE}/v1/chat/completions",
            method="POST",
            body={
                "model": FUNCTIONAL_MODEL,
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "max_tokens": 512,
            },
            headers={"Authorization": f"Bearer {gateway_key}"},
            timeout=180,
        )
        if status != 200:
            raise RuntimeError(f"gateway chat probe failed: HTTP {status}: {json.dumps(body)[:200]}")
        usage = body.get("usage") or {}
        row_log = db_one(
            "SELECT channel_id FROM logs WHERE model_name = ? AND created_at > ? AND type=2 "
            "ORDER BY id DESC LIMIT 1",
            (FUNCTIONAL_MODEL, mark),
        )
        attr = int(row_log[0]) if row_log else None
        if attr is not None:
            break
        if attempt < 5:
            time.sleep(5)
    created_ids = {cid for cid, _ in created}
    if attr is None:
        print("WARN: gateway rotation probe produced no log row after retries (pool previously proven)")
    else:
        print(
            f"rotation note: {FUNCTIONAL_MODEL} gateway chat 200 usage={usage.get('prompt_tokens')}/"
            f"{usage.get('completion_tokens')} attributed ch{attr} "
            f"({'new pool member' if attr in created_ids else 'existing pool member'})"
        )

    print(f"DONE: pool expanded with {', '.join(f'ch{cid} {name}' for cid, name in created)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
