#!/usr/bin/env python3
"""Onboard 基元律动 (tokenrhythm.studio) key pool as N single-key channels (2026-10-04).

Shape follows scripts/ops/add_nimbridge_relay_channel.py (idempotent resume).

Design (evidence-backed):
- Endpoint: https://tokenrhythm.studio (23-model catalog). WoTrus CA is
  distrusted by Mozilla-class root bundles (fork embedded roots) -> all
  traffic rides the local bridge http://127.0.0.1:8792 (see
  docs/ops/nimbridge-bridge-2026-10-04.md).
- MULTI-KEY REFUTED (2026-10-04, two creation probes): this fork does NOT
  set channel_info.is_multi_key on single-mode POST with newline- or
  CRLF-joined keys; the blob is stored as ONE invalid key -> every call
  fails instantly ("do_request failed", time=0, no upstream log). So the
  pool goes in as N single-key channels (agentrouter-claude-keypool
  precedent), one per VERIFIED-good key (21/37 passed a live-balance sweep;
  indices in the runbook, values never printed).
- Scope (user-approved gap-driven set): k3 (map kimi-k3), qwen3.7-max,
  qwen3.8-max, glm-5.3, glm-5.3-flash, deepseek-v4-flash (map deepseek-flash),
  longcat-2.0.
- Posture: priority -10 / weight 1 bottom-tier backup (matches ch148/ch149).
  auto_ban=1 covers auth/quota-class rejections only (5xx NOT covered).

Dry-run by default; --apply mutates. Keys file path via TR_KEYS_FILE
(default: D:/Downloads/基元律动.txt). Key values never printed.
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
DEFAULT_KEYS_FILE = Path("D:/Downloads/基元律动.txt")

BASE_URL = "http://127.0.0.1:8792"  # WoTrus CA distrusted by fork roots -> bridge
CHANNEL_PREFIX = "tokenrhythm-k"
# 1-based indices of the 21 keys that passed the 2026-10-04 live-balance sweep
GOOD_KEY_INDICES = [3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 19, 20, 22, 26, 29, 30, 31, 32, 34]
MODELS = [
    "k3",
    "qwen3.7-max",
    "qwen3.8-max",
    "glm-5.3",
    "glm-5.3-flash",
    "deepseek-v4-flash",
    "longcat-2.0",
]
MODEL_MAPPING = {"k3": "kimi-k3", "deepseek-v4-flash": "deepseek-flash"}
SPOT_TESTS = {1: "glm-5.3", 7: "qwen3.7-max", 13: "longcat-2.0", 21: "k3"}  # channel seq -> model
PRIMARY_MODEL = "glm-5.3"
PRIORITY = -10
WEIGHT = 1


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def db_all(sql, params=()):
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as c:
        return c.execute(sql, params).fetchall()


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = backup_dir / f"new-api-before-tokenrhythm-{stamp}.db"
    with closing(sqlite3.connect(str(db_path))) as source:
        with closing(sqlite3.connect(str(destination))) as target:
            source.backup(target)
    with closing(sqlite3.connect(f"file:{destination.as_posix()}?mode=ro", uri=True)) as check:
        row = check.execute("PRAGMA integrity_check").fetchone()
    if not row or row[0] != "ok":
        raise RuntimeError(f"backup integrity check failed: {row!r}")
    return destination


def fetch_channels(smoke, headers):
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=300", headers=headers
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"channel list failed: HTTP {status}")
    items = (body.get("data") or {}).get("items") or []
    return [i for i in items if isinstance(i, dict)]


def read_gateway_key() -> str:
    import re

    text = (Path.home() / ".omp" / "agent" / "models.yml").read_text(encoding="utf-8")
    match = re.search(r"zg-newapi.*?apiKey:\s*(\S+)", text, re.S)
    if not match:
        raise RuntimeError("zg-newapi apiKey not found in models.yml")
    return match.group(1)


def load_keys(path: Path) -> list[str]:
    if not path.exists():
        raise RuntimeError(f"keys file missing: {path}")
    keys = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("sk_tr_") and line not in keys:
            keys.append(line)
    if not keys:
        raise RuntimeError(f"no sk_tr_ keys found in {path}")
    return keys


def channel_name(seq: int) -> str:
    return f"{CHANNEL_PREFIX}{seq:02d}"


def config_matches(ch: dict) -> list[str]:
    mismatch = []
    if str(ch.get("base_url") or "") != BASE_URL:
        mismatch.append("base_url")
    if str(ch.get("models") or "") != ",".join(MODELS):
        mismatch.append("models")
    if int(ch.get("priority") or 0) != PRIORITY:
        mismatch.append("priority")
    stored_raw = ch.get("model_mapping")
    stored_obj = stored_raw if isinstance(stored_raw, dict) else json.loads(str(stored_raw or "") or "{}")
    if stored_obj != MODEL_MAPPING:
        mismatch.append("model_mapping")
    return mismatch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    args = parser.parse_args()

    keys_file = Path(os.environ.get("TR_KEYS_FILE", str(DEFAULT_KEYS_FILE)))
    all_keys = load_keys(keys_file)
    good = [(seq, all_keys[idx - 1]) for seq, idx in enumerate(GOOD_KEY_INDICES, start=1)]
    print(f"keys: {len(all_keys)} total, {len(good)} verified-good (balance sweep 2026-10-04; values never printed)")
    print("plan:")
    print(f"  create: {len(good)} single-key channels {CHANNEL_PREFIX}01..{CHANNEL_PREFIX}{len(good):02d} "
          f"models={len(MODELS)} each prio={PRIORITY} w={WEIGHT}")
    print(f"  mapping: {json.dumps(MODEL_MAPPING)}  base={BASE_URL} (WoTrus bridge)")
    print(f"  functional: abilities {len(good)}x{len(MODELS)} rows + spot admin tests {SPOT_TESTS} + k3 chain-health")

    if not args.apply:
        print("dry-run: no changes made")
        return 0

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    backup = online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    # 1. create single-key channels (skip existing with matching config)
    channels = fetch_channels(smoke, headers)
    by_name = {str(c.get("name")): c for c in channels}
    id_by_seq: dict[int, int] = {}
    for seq, key in good:
        name = channel_name(seq)
        existing = by_name.get(name)
        if existing is not None:
            mismatch = config_matches(existing)
            if mismatch:
                raise RuntimeError(f"{name} (ch{existing['id']}) exists with drift: {mismatch}; manual fix required")
            id_by_seq[seq] = int(existing["id"])
            print(f"resume: {name} = ch{id_by_seq[seq]} (exists)")
            continue
        payload = {
            "name": name,
            "type": 1,
            "base_url": BASE_URL,
            "key": key,
            "models": ",".join(MODELS),
            "model_mapping": json.dumps(MODEL_MAPPING, ensure_ascii=False),
            "group": "default",
            "priority": PRIORITY,
            "weight": WEIGHT,
            "auto_ban": 1,
            "test_model": PRIMARY_MODEL,
        }
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/",
            method="POST",
            body={"mode": "single", "channel": payload},
            headers=headers,
        )
        if status != 200 or not isinstance(body, dict) or not body.get("success"):
            message = body.get("message") if isinstance(body, dict) else None
            raise RuntimeError(f"{name} POST failed: HTTP {status} message={message!r}")
        after = fetch_channels(smoke, headers)
        match = [c for c in after if str(c.get("name")) == name]
        if not match:
            raise RuntimeError(f"{name} not visible after POST")
        id_by_seq[seq] = int(match[0]["id"])
        print(f"created {name} = ch{id_by_seq[seq]}")

    # 2. fix + verify abilities in aggregate
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/fix", method="POST", body={}, headers=headers
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"channel fix failed: HTTP {status} message={message!r}")
    ids = sorted(id_by_seq.values())
    rows = []
    placeholders = ",".join("?" for _ in ids)
    deadline = time.monotonic() + 120
    expected = len(ids) * len(MODELS)
    while time.monotonic() < deadline:
        rows = db_all(
            f'SELECT COUNT(*) FROM abilities WHERE channel_id IN ({placeholders}) '
            f'AND "group" = ? AND enabled = 1 AND priority = ? AND weight = ?',
            (*ids, "default", PRIORITY, WEIGHT),
        )
        if rows and int(rows[0][0]) == expected:
            break
        time.sleep(3)
    else:
        got = rows[0][0] if rows else 0
        raise RuntimeError(f"abilities aggregate mismatch: {got} rows, expected {expected}")
    print(f"verify ok: abilities {len(ids)}ch x {len(MODELS)} models = {expected} rows (default,1,{PRIORITY},{WEIGHT})")

    # 3. spot admin tests across the pool (each traverses mapping where applicable)
    for seq, model in SPOT_TESTS.items():
        cid = id_by_seq[seq]
        t_status, t_body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/test/{cid}?model={model}",
            headers=headers,
            timeout=180,
        )
        t_ok = isinstance(t_body, dict) and t_body.get("success")
        t_ms = t_body.get("time") if isinstance(t_body, dict) else None
        print(f"channel test {channel_name(seq)}/ch{cid} {model}: HTTP {t_status} success={t_ok} time={t_ms}")
        if not t_ok:
            message = t_body.get("message") if isinstance(t_body, dict) else None
            raise RuntimeError(f"channel test failed for {channel_name(seq)} {model}: {message!r}")

    # 4. chain-health: k3 must route to primary ch33, not the new backups
    gateway_key = read_gateway_key()
    mark = time.time()
    status, body = smoke.http_json(
        f"{GATEWAY_BASE}/v1/chat/completions",
        method="POST",
        body={
            "model": "k3",
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": 16,
        },
        headers={"Authorization": f"Bearer {gateway_key}"},
        timeout=180,
    )
    if status != 200:
        raise RuntimeError(f"k3 gateway chat failed: HTTP {status}: {json.dumps(body)[:200]}")
    attr = None
    for _ in range(6):
        row_log = db_all(
            "SELECT channel_id FROM logs WHERE model_name = ? AND created_at > ? AND type = 2 "
            "ORDER BY id DESC LIMIT 1",
            ("k3", mark),
        )
        if row_log:
            attr = int(row_log[0][0])
            break
        time.sleep(5)
    if attr in set(ids):
        raise RuntimeError(f"unexpected: backup ch{attr} served k3 while primary ch33 is enabled")
    print(f"chain-health OK: k3 -> ch{attr} (primary; new backups stayed idle)")

    print(f"DONE: {len(ids)} channels {channel_name(1)}..{channel_name(len(ids))} live; models: {','.join(MODELS)}")
    print("NEXT: run smoke gate, write runbook")
    return 0


if __name__ == "__main__":
    sys.exit(main())
