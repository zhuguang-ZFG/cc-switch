#!/usr/bin/env python3
"""Clamp reasoning_effort=max -> xhigh on ch15 (sensenova-token) conditionally.

Problem (2026-08-22 logs): OMP fallback carries the session's reasoning
effort verbatim. muse-free/x-preview-f-free allow effort "max"; when OMP
falls back to deepseek-v4-flash (pool main = ch15), ch15's upstream rejects
it: 400 `field ReasoningEffort invalid, should be one of: low, medium, high,
xhigh, none` (6 hits 17:45~20:26). OMP sends top-level `reasoning_effort`
(verified in ~/.omp/logs/http-400-requests/*.json).

Fix: new-api param_override operations DSL (verified against upstream
relay/common/override.go): conditions support full/prefix/suffix/contains/
gt/gte/lt/lte + invert, operations support set/delete/replace etc. We add a
single conditional op: when body reasoning_effort == "max", set it to
"xhigh" (the highest value the upstream accepts). All other efforts pass
through untouched, so main-path max on muse/x-preview-f is unaffected and
OMP models.yml whitelists stay as-is.

Scope: ch15 only — it is the only channel that logged this 400 in 24h.

Workflow: reproduce-first (relay probe with reasoning_effort=max must 400
before the change; if it already 200s, abort as no-op), whole-DB snapshot
backup, PUT full channel projection minus status with param_override added,
75s cache sync, post probes (max -> must 200; xhigh -> must 200; no effort
-> must 200), readback verify param_override persisted. Rollback on failure:
restore the original param_override. Re-running after success exits as
already-applied.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")

CHANNEL_ID = 15
CHANNEL_NAME = "sensenova-token"
CACHE_SYNC_SECONDS = 75
OMP_MODELS_YML = Path.home() / ".omp" / "agent" / "models.yml"
PROBE_MODEL = "deepseek-v4-flash"  # pool main is ch15 (p50)

CLAMP_OPERATION = {
    "mode": "set",
    "path": "reasoning_effort",
    "value": "xhigh",
    "keep_origin": False,
    "logic": "AND",
    "conditions": [
        {"mode": "full", "path": "reasoning_effort", "value": "max"}
    ],
}
PARAM_OVERRIDE = json.dumps({"operations": [CLAMP_OPERATION]})


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


def put_channel(smoke, headers: dict[str, str], payload: dict) -> None:
    # UpdateChannel binds the channel struct from the body directly; it
    # expects the full list-API projection minus status (see
    # rotate_ai168661_ox_alpha_key.py for the pitfalls).
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/",
        method="PUT",
        body=payload,
        headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"channel PUT failed: HTTP {status} message={message!r}")


def read_omp_relay_token() -> str:
    text = OMP_MODELS_YML.read_text(encoding="utf-8")
    match = re.search(r"^\s*apiKey:\s*(sk-\S+)\s*$", text, re.MULTILINE)
    if match is None:
        raise RuntimeError(f"no sk- apiKey found in {OMP_MODELS_YML}")
    return match.group(1)


def relay_call(smoke, effort: str | None) -> tuple[int, str]:
    """Relay probe through 3002; returns (http_status, short_detail)."""
    token = read_omp_relay_token()
    body = {
        "model": PROBE_MODEL,
        "messages": [{"role": "user", "content": "say OK"}],
        "max_tokens": 32,
    }
    if effort is not None:
        body["reasoning_effort"] = effort
    status, resp = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/v1/chat/completions",
        method="POST",
        body=body,
        headers={"Authorization": f"Bearer {token}"},
        timeout=90,
    )
    if status == 200 and isinstance(resp, dict) and resp.get("choices"):
        return 200, "ok"
    text = json.dumps(resp) if isinstance(resp, (dict, list)) else str(resp)
    return status, text[:160]


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        f"new-api-before-ch15-effort-clamp-{time.strftime('%Y%m%d-%H%M%S')}.db"
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


def current_override_db(db_path: Path) -> str:
    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        row = connection.execute(
            "SELECT param_override FROM channels WHERE id = ?", (CHANNEL_ID,)
        ).fetchone()
    if row is None:
        raise RuntimeError(f"ch{CHANNEL_ID} missing in DB")
    return row[0] or ""


def override_matches(raw: str) -> bool:
    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    ops = parsed.get("operations")
    return isinstance(ops, list) and CLAMP_OPERATION in ops


def main() -> int:
    args = parse_args()
    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    token, user_id = smoke.admin_auth()
    headers = {
        "Authorization": f"Bearer {token}",
        "New-Api-User": str(user_id),
    }

    items = list_channels(smoke, headers)
    channel = next((i for i in items if i.get("id") == CHANNEL_ID), None)
    if channel is None:
        raise RuntimeError(f"ch{CHANNEL_ID} not found via API")
    if channel.get("name") != CHANNEL_NAME:
        raise RuntimeError(
            f"ch{CHANNEL_ID} name drifted: {channel.get('name')!r} "
            f"(expected {CHANNEL_NAME!r})"
        )

    already = override_matches(current_override_db(db_path))
    print(
        f"plan: ch{CHANNEL_ID} {CHANNEL_NAME} param_override "
        f"{'already contains clamp (verify only)' if already else '+= conditional max->xhigh clamp'}"
    )
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    if already:
        status, detail = relay_call(smoke, "max")
        if status != 200:
            raise RuntimeError(
                f"clamp present but max probe still fails: HTTP {status} {detail!r}"
            )
        print(f"OK: already applied; max probe 200; DB untouched")
        return 0

    # Reproduce-first: the bug must be live before we touch anything.
    status, detail = relay_call(smoke, "max")
    print(f"pre-change max probe: HTTP {status} {detail!r}")
    if status == 200:
        print("no-op: max already accepted upstream; nothing to fix")
        return 0
    if status != 400:
        raise RuntimeError(
            f"unexpected pre-change failure mode HTTP {status}; "
            f"aborting rather than poking a different bug"
        )

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    original_override = current_override_db(db_path)
    payload = {k: v for k, v in channel.items() if k != "status"}
    if original_override.strip():
        raise RuntimeError(
            f"ch{CHANNEL_ID} already has a param_override this script does not "
            f"know how to merge: {original_override[:120]!r}"
        )
    payload["param_override"] = PARAM_OVERRIDE

    applied = False
    try:
        put_channel(smoke, headers, payload)
        applied = True
        print(f"ch{CHANNEL_ID} param_override set (conditional max->xhigh)")

        print(f"waiting {CACHE_SYNC_SECONDS}s for channel cache sync")
        time.sleep(CACHE_SYNC_SECONDS)

        for effort, label in (("max", "max->clamp"), ("xhigh", "xhigh passthrough"), (None, "no effort")):
            status, detail = relay_call(smoke, effort)
            print(f"post-change probe {label}: HTTP {status} {detail!r}")
            if status != 200:
                raise RuntimeError(
                    f"post-change probe {label} failed: HTTP {status} {detail!r}"
                )

        if not override_matches(current_override_db(db_path)):
            raise RuntimeError("readback: param_override did not persist")
        print(
            f"OK: ch{CHANNEL_ID} clamp live and verified; backup={backup.name}"
        )
        return 0
    except Exception:
        if applied:
            try:
                rollback_payload = {
                    k: v for k, v in channel.items() if k != "status"
                }
                rollback_payload["param_override"] = original_override
                put_channel(smoke, headers, rollback_payload)
                print(f"rollback: ch{CHANNEL_ID} param_override restored")
            except Exception as error:
                print(f"rollback warning: restore failed: {error}")
        print(f"rollback attempted; full snapshot={backup.name}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
