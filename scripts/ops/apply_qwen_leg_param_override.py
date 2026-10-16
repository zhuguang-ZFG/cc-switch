#!/usr/bin/env python3
"""Delete `prompt_cache_key` on the remaining qwen3-8-27b legs (ch124 groq, ch171 obitmc).

Why channel-level (house precedent, 2026-10-16): OMP's chat/completions transport always
injects `prompt_cache_key` (pi-ai applyOpenAIChatCompletionsPromptCachePolicy) and there is
no models.yml suppressor for it -- a per-model `compat.supportsPromptCacheKey: false` is
dropped because the resolved shape does not declare that key (verified live: the 400
persisted). runinfra (ch88) already carries exactly this param_override for the same
reason; this script applies it to the two remaining legs of the same model id so the
model is usable end-to-end from OMP.

Contract (mirrors park_zombie_channels_20261007.py):
  - identity check per channel (id -> expected name) before any write
  - whole-DB online snapshot backup before any change
  - the admin GET redacts `key`; the real key is re-attached from the local SSOT
  - PUT /api/channel/ with the hydrated record (status excluded, as in the park script)
  - readback verification: param_override parses to the expected operations

Dry run by default; pass --apply to write.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

OPS_DIR = Path(__file__).resolve().parent
SMOKE_PATH = OPS_DIR / "newapi-local-smoke.py"
DB_PATH = Path.home() / ".new-api-local" / "new-api.db"
SECRETS_PATH = Path.home() / ".omp" / "guardian" / "secrets.json"

TARGETS = {124: "groq-qwen3.8-27b", 171: "obitmc-relay"}
PARAM_OVERRIDE = json.dumps({"operations": [{"path": "prompt_cache_key", "mode": "delete"}]})
# groq's on-demand tier cannot take OMP's prompt envelope: its 413 "Request too large"
# fires on the full base prompt + tools even though direct probes pass up to
# max_completion_tokens=16384 (verified 2026-10-16), and 413 is not in the retryable
# set, so a groq-primary route hard-fails every turn. runinfra (ch88, p49) is the leg
# built for OMP; demote groq below it while keeping it as a fast small-request leg.
PRIORITY_CHANGES = {124: 45}


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load smoke helpers from {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def online_backup(db_path: Path) -> Path:
    destination = db_path.with_name(
        f"{db_path.stem}-before-qwen-leg-override-{time.strftime('%Y%m%d-%H%M%S')}{db_path.suffix}"
    )
    with closing(sqlite3.connect(str(db_path))) as source, closing(sqlite3.connect(str(destination))) as target:
        with target:
            source.backup(target)
    return destination


def usable_key(value: str) -> bool:
    stripped = value.strip()
    return bool(stripped) and not set(stripped) <= {"*", "•", "."}


def hydrate_key(channel: dict, db_path: Path) -> dict:
    channel_id = channel.get("id")
    channel_name = channel.get("name")
    if not isinstance(channel_id, int) or not isinstance(channel_name, str):
        raise RuntimeError("channel identity is incomplete")
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)) as connection:
        row = connection.execute("SELECT name, key FROM channels WHERE id = ?", (channel_id,)).fetchone()
    if not row or row[0] != channel_name:
        raise RuntimeError(f"channel {channel_id} identity mismatch in local SSOT")
    row_key = row[1]
    if not isinstance(row_key, str) or not usable_key(row_key):
        raise RuntimeError(f"channel {channel_id}: local SSOT has no usable key to re-attach")
    hydrated = dict(channel)
    hydrated["key"] = row_key
    return hydrated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    smoke = load_smoke()
    secrets = json.loads(SECRETS_PATH.read_text(encoding="utf-8-sig"))
    token = str(secrets.get("newapi_token", ""))
    user_id = str(secrets.get("newapi_user", "1"))
    if not token:
        raise RuntimeError("guardian secrets.json has no newapi_token")
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": user_id}

    plan = []
    for cid, expected_name in TARGETS.items():
        st, body = smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/{cid}", headers=headers)
        channel = (body or {}).get("data") if isinstance(body, dict) else None
        if st != 200 or not isinstance(channel, dict) or channel.get("name") != expected_name:
            raise RuntimeError(
                f"ch{cid} identity mismatch: HTTP {st} "
                f"name={channel.get('name') if isinstance(channel, dict) else None!r}"
            )
        current = channel.get("param_override")
        try:
            current_ops = json.loads(current or "null")
        except ValueError:
            current_ops = f"<unparsable: {str(current)[:60]}>"
        plan.append(channel)
        print(
            f"plan ch{cid} {expected_name}: status={channel.get('status')} "
            f"p={channel.get('priority')} w={channel.get('weight')} "
            f"param_override={current_ops!r} -> delete prompt_cache_key"
        )
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes)")

    expected = json.loads(PARAM_OVERRIDE)
    for channel in plan:
        cid = int(channel["id"])
        hydrated = hydrate_key(channel, DB_PATH)
        updated = {key: value for key, value in hydrated.items() if key != "status"}
        updated["param_override"] = PARAM_OVERRIDE
        st, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/", method="PUT", body=updated, headers=headers
        )
        if st != 200 or not isinstance(body, dict) or not body.get("success"):
            raise RuntimeError(f"ch{cid} PUT(param_override) failed: HTTP {st} body={str(body)[:220]}")
        st, body = smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/{cid}", headers=headers)
        readback = (body or {}).get("data") if isinstance(body, dict) else None
        if st != 200 or not isinstance(readback, dict):
            raise RuntimeError(f"ch{cid} readback failed: HTTP {st}")
        if json.loads(readback.get("param_override") or "null") != expected:
            raise RuntimeError(f"ch{cid} param_override readback mismatch: {readback.get('param_override')!r}")
        if readback.get("name") != channel.get("name") or readback.get("models") != channel.get("models"):
            raise RuntimeError(f"ch{cid} identity drifted after PUT")
        print(f"ch{cid} {readback.get('name')}: PUT ok, param_override verified")
        new_priority = PRIORITY_CHANGES.get(cid)
        if new_priority is not None and readback.get("priority") != new_priority:
            updated = {key: value for key, value in hydrated.items() if key != "status"}
            updated["priority"] = new_priority
            st, body = smoke.http_json(
                f"{smoke.NEWAPI_BASE}/api/channel/", method="PUT", body=updated, headers=headers
            )
            if st != 200 or not isinstance(body, dict) or not body.get("success"):
                raise RuntimeError(f"ch{cid} PUT(priority) failed: HTTP {st} body={str(body)[:220]}")
            st, body = smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/{cid}", headers=headers)
            readback2 = (body or {}).get("data") if isinstance(body, dict) else None
            if st != 200 or not isinstance(readback2, dict) or readback2.get("priority") != new_priority:
                raise RuntimeError(f"ch{cid} priority readback mismatch: {readback2 and readback2.get('priority')!r}")
            print(f"ch{cid} priority {channel.get('priority')} -> {new_priority} (readback ok)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
