#!/usr/bin/env python3
"""Rotate the zzzcoding claude-opus-5 key on ch123 (type=14 Anthropic endpoint).

The user-provided ANTHROPIC key for https://api.zzzcoding.org replaces the
stored ch123 key. ch123 is the p0/w1 fallback tier for claude-opus-5, gated by
zz_gate.py: status=2 while the upstream subscription pool is empty (503
"No available accounts"), auto-enabled only while a pool window is open.

The new key is NEVER printed, written to the repo, or placed on argv. It is
read from the ZZ_KEY environment variable (same convention as
setup_zzzcoding_claude.py).

These channels pin every other field from live state and replace only the key
(UpdateChannel binds the full projection; partial bodies return
"Invalid parameters", and the {"mode",...} wrapper returns "record not found").

Upstream key validity is proven by a direct /v1/models probe (must be HTTP
200 and list claude-opus-5). The /v1/messages management probe is NOT used:
while the pool is empty it returns 503 no-accounts regardless of key validity
(that is the gate's "down" signal, not a key failure).

Usage:
  ZZ_KEY=sk-... python3 scripts/ops/rotate_zzzcoding_claude_key.py            # dry-run
  ZZ_KEY=sk-... python3 scripts/ops/rotate_zzzcoding_claude_key.py --apply    # apply
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

CHANNEL_NAME = "zzzcoding-claude-opus-5"
CHANNEL_ID = 123
BASE_URL = "https://api.zzzcoding.org"
MODELS = "claude-opus-5"
UPSTREAM_CATALOG_URL = f"{BASE_URL}/v1/models"


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mask(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]} (len={len(key)})"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the backed-up live change; default is read-only",
    )
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
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise RuntimeError("channel list has invalid shape")
    return items


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        f"new-api-before-zzzcoding-claude-key-{time.strftime('%Y%m%d-%H%M%S')}.db"
    )
    if destination.exists():
        raise RuntimeError(f"backup already exists: {destination}")
    source = sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )
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


def direct_catalog_probe(key: str) -> None:
    """Prove the new key reaches the upstream catalog; fail on anything else."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(UPSTREAM_CATALOG_URL)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("User-Agent", "claude-cli/2.1.251 (external, sdk-cli)")
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"upstream catalog probe failed: HTTP {exc.code} "
            f"{exc.read().decode(errors='replace')[:120]!r}"
        ) from exc
    except Exception as exc:  # network/DNS/TLS
        raise RuntimeError(f"upstream catalog probe error: {exc}") from exc
    ids = {m.get("id") for m in body.get("data", []) if isinstance(m, dict)}
    if "claude-opus-5" not in ids:
        raise RuntimeError(f"catalog does not list claude-opus-5: {sorted(ids)}")


def main() -> int:
    args = parse_args()
    key = os.environ.get("ZZ_KEY", "").strip()
    if not key:
        print("abort: ZZ_KEY environment variable not set (key never goes on argv)", file=sys.stderr)
        return 1

    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    token, user_id = smoke.admin_auth()
    headers = {
        "Authorization": f"Bearer {token}",
        "New-Api-User": str(user_id),
    }
    items = list_channels(smoke, headers)
    named = [i for i in items if i.get("name") == CHANNEL_NAME and i.get("id") == CHANNEL_ID]
    if len(named) != 1:
        raise RuntimeError(
            f"expected exactly ch{CHANNEL_ID} {CHANNEL_NAME!r}, got "
            f"{[ (i.get('id'), i.get('name')) for i in items if i.get('name') == CHANNEL_NAME]}"
        )
    channel = named[0]
    status_now = channel.get("status")
    if not isinstance(status_now, int):
        raise RuntimeError(f"ch{CHANNEL_ID} status unavailable in API projection")

    # Composite identity lock: id + name both enforced, per the Guardian PUT rule.
    for field, expect in (("name", CHANNEL_NAME), ("base_url", BASE_URL),
                          ("models", MODELS), ("type", 14)):
        if channel.get(field) != expect:
            raise RuntimeError(f"ch{CHANNEL_ID} {field} mismatch: {channel.get(field)!r} != {expect!r}")

    print(
        f"plan: rotate key on ch{CHANNEL_ID} {CHANNEL_NAME} "
        f"(new key={mask(key)}); name/type/base_url/models/status={status_now} "
        f"pinned unchanged"
    )
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    # Key validity BEFORE any DB write: catalog reachable and lists the model.
    direct_catalog_probe(key)
    print("upstream catalog probe ok: HTTP 200, claude-opus-5 listed")

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    payload = {k: v for k, v in channel.items() if k != "status"}
    payload["key"] = key
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/",
        method="PUT",
        body=payload,
        headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"update failed: HTTP {status} message={message!r}")
    print(f"ch{CHANNEL_ID} key rotated")

    # Readback verify: key changed; everything else pinned unchanged.
    items = list_channels(smoke, headers)
    back = next((i for i in items if i.get("id") == CHANNEL_ID), None)
    if back is None:
        raise RuntimeError(f"ch{CHANNEL_ID} missing on readback")
    expected = {
        "name": CHANNEL_NAME,
        "type": 14,
        "status": status_now,
        "base_url": BASE_URL,
        "models": MODELS,
        "auto_ban": 1,
        "priority": channel.get("priority"),
        "weight": channel.get("weight"),
    }
    mismatch = {
        field: (back.get(field), value)
        for field, value in expected.items()
        if back.get(field) != value
    }
    if back.get("test_model") not in (None, ""):
        mismatch["test_model"] = ("unexpected non-empty test_model", back.get("test_model"))

    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        stored = connection.execute(
            "SELECT key FROM channels WHERE id=?", (CHANNEL_ID,)
        ).fetchone()
        ability = connection.execute(
            "SELECT enabled, priority, weight, \"group\" FROM abilities "
            "WHERE channel_id=? AND model=?",
            (CHANNEL_ID, MODELS),
        ).fetchone()
    if stored is None or stored[0] != key:
        mismatch["db_key"] = ("did not persist", "mismatch")
    if ability != (0, 0, 1, "default"):
        # status=2 => ability enabled=0 with the p0/w1 tier posture.
        mismatch["ability"] = (ability, "(0, 0, 1, 'default')")
    if mismatch:
        raise RuntimeError(
            f"readback mismatch for ch{CHANNEL_ID}: {mismatch}"
        )
    print(
        f"OK: ch{CHANNEL_ID} {CHANNEL_NAME} key rotated, catalog probe green, "
        f"all other fields + ability row verified unchanged; backup={backup.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())