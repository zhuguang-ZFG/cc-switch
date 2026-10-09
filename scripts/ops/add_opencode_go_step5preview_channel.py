#!/usr/bin/env python3
"""Onboard Step-5-Preview from the OpenCode Go plan (zen/go endpoint).

Context (2026-10-08): OpenCode announced Step-5-Preview as a limited-time
free model inside OpenCode / OpenCode Go for one week — 1M context,
multi-modal, ZDR, and explicitly "does not consume Go plan usage".

The Go catalog (donor key, ch130) lists the campaign id as
``step-5-preview-free`` (39th model family; verified live 2026-10-08):
- /v1/models -> 200, id present;
- chat/completions non-stream -> 200 content 'GO_STEP_OK' (native
  ``reasoning``/``reasoning_content``, usage with cache details);
- stream -> 200, 113 SSE frames + [DONE] + usage;
- image_url (1x1 png) -> 200 correct answer ('Red') => multimodal OK;
- ``reasoning_effort=max`` accepted (reasoning_tokens reported);
- ``max_tokens=131072`` accepted; structured ``tool_calls`` decode OK.

This script therefore mirrors the ch131/132/133/137 Go-family posture:
type=1, base_url/header_override(User-Agent + x-opencode-session)/key
cloned from the ch130 donor (never printed, never modified), priority 0,
weight 2, auto_ban=1, ModelRatio=0 (free campaign, zero marginal cost).

Two ids are exposed on the channel:
- ``step-5-preview-free`` — exact upstream id (honest, primary OMP entry);
- ``step-5-preview``      — alias mapped to -free, so the canonical
  id also resolves through the gateway (id parity with the arcdent
  provider entry in OMP, for cheap provider re-pointing later).

Workflow contract (same as the other add_* scripts): dup check, whole-DB
snapshot backup, create disabled, management probe while disabled, enable
only after probe passes, channel + abilities + ModelRatio readback verify,
then a relay probe through 127.0.0.1:3002 with the OMP zg-newapi token
(never printed) plus consumption-log channel attribution for BOTH ids.
Re-running is verify-only and never touches an existing channel's status.

Usage: dry-run by default; ``--apply`` executes.
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

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
DB_PATH = Path.home() / ".new-api-local" / "new-api.db"
GATEWAY_BASE = "http://127.0.0.1:3002"

CHANNEL_NAME = "opencode-go-step-5-preview"
KEY_DONOR = 130  # opencode-go-space-bunny-free: key + header_override + base_url donor
UPSTREAM_MODEL = "step-5-preview-free"
ALIAS_MODEL = "step-5-preview"
MODELS = f"{UPSTREAM_MODEL},{ALIAS_MODEL}"
MODEL_MAPPING = json.dumps({ALIAS_MODEL: UPSTREAM_MODEL}, separators=(",", ":"))
TEST_MODEL = UPSTREAM_MODEL
PRIORITY = 0
WEIGHT = 2
MODEL_RATIO = 0  # 免费限时活动，零边际成本
OMP_MODELS_YML = Path.home() / ".omp" / "agent" / "models.yml"


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


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        f"new-api-before-opencode-go-step5preview-{time.strftime('%Y%m%d-%H%M%S')}.db"
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
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=200",
        headers=headers,
        timeout=30,
    )
    if status != 200:
        raise RuntimeError(f"channel list failed: HTTP {status}")
    items = body.get("data", {}).get("items") or body.get("data") or []
    return [i for i in items if isinstance(i, dict)]


def read_gateway_key() -> str:
    """OMP zg-newapi apiKey from live models.yml (never printed)."""
    import re

    text = OMP_MODELS_YML.read_text(encoding="utf-8")
    match = re.search(r"^  zg-newapi:\n(?:    .*\n)*?    apiKey:\s*(\S+)", text, flags=re.M)
    if not match:
        raise RuntimeError("zg-newapi apiKey not found in live models.yml")
    return match.group(1)


def set_status(smoke, headers, channel_id: int, status: int) -> None:
    code, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}/status",
        method="POST",
        body={"status": status},
        headers=headers,
    )
    if code != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"channel {channel_id} status={status} failed: HTTP {code}")


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


def gateway_chat(smoke, gateway_key: str, model: str, mark: float) -> tuple[int, dict]:
    status, body = smoke.http_json(
        f"{GATEWAY_BASE}/v1/chat/completions",
        method="POST",
        body={
            "model": model,
            "messages": [{"role": "user", "content": "Reply with exactly: STEP_OK"}],
            "max_tokens": 800,
        },
        headers={"Authorization": f"Bearer {gateway_key}"},
        timeout=180,
    )
    if status != 200:
        raise RuntimeError(f"gateway {model} probe failed: HTTP {status}: {json.dumps(body)[:200]}")
    row = db_one(
        "SELECT channel_id FROM logs WHERE model_name = ? AND created_at > ? AND type = 2 "
        "ORDER BY id DESC LIMIT 1",
        (model, mark),
    )
    return (int(row[0]) if row else -1), body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    args = parser.parse_args()

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    channels = fetch_channels(smoke, headers)
    by_id = {int(c["id"]): c for c in channels}
    by_name = {str(c.get("name")): c for c in channels}

    # preflight
    if KEY_DONOR not in by_id:
        raise RuntimeError(f"ch{KEY_DONOR} key donor missing")
    donor = by_id[KEY_DONOR]
    header_override = donor.get("header_override")
    if not header_override or "x-opencode-session" not in str(header_override):
        raise RuntimeError(f"ch{KEY_DONOR} header_override missing session header; refusing to clone")
    base_url = str(donor.get("base_url") or "").rstrip("/")
    if "opencode.ai/zen/go" not in base_url:
        raise RuntimeError(f"ch{KEY_DONOR} base_url unexpected: {base_url}")
    donor_row = db_one("SELECT key FROM channels WHERE id = ?", (KEY_DONOR,))
    if not donor_row or not donor_row[0]:
        raise RuntimeError(f"ch{KEY_DONOR} key not readable from DB")

    resume = by_name.get(CHANNEL_NAME)
    if resume is None:
        for model in (UPSTREAM_MODEL, ALIAS_MODEL):
            live = db_one("SELECT channel_id FROM abilities WHERE model = ? AND enabled = 1", (model,))
            if live:
                raise RuntimeError(f"{model} already has an enabled ability: ch{live[0]}")

    print("plan:")
    print(f"  create: {CHANNEL_NAME} models={MODELS} mapping={MODEL_MAPPING} prio={PRIORITY} w={WEIGHT}")
    print(f"  key/header_override/base_url cloned from ch{KEY_DONOR} (donor not modified)")
    print(f"  ModelRatio[{UPSTREAM_MODEL},{ALIAS_MODEL}] = {MODEL_RATIO}; POST /api/channel/fix")
    print("  functional: management probe (disabled) -> enable -> gateway chat both ids + log attribution")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    if resume is None:
        payload = {
            "name": CHANNEL_NAME,
            "type": 1,
            "base_url": base_url,
            "key": donor_row[0],
            "models": MODELS,
            "group": "default",
            "header_override": header_override,
            "model_mapping": MODEL_MAPPING,
            "test_model": TEST_MODEL,
            "priority": PRIORITY,
            "weight": WEIGHT,
            "status": 2,  # created disabled; enabled only after probe passes
            "auto_ban": 1,
        }
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/",
            method="POST",
            body={"mode": "single", "channel": payload},
            headers=headers,
            timeout=30,
        )
        if status != 200 or not isinstance(body, dict) or not body.get("success"):
            message = body.get("message") if isinstance(body, dict) else None
            raise RuntimeError(f"channel POST failed: HTTP {status} message={message!r}")
        after = fetch_channels(smoke, headers)
        match = [c for c in after if str(c.get("name")) == CHANNEL_NAME]
        if not match:
            raise RuntimeError(f"{CHANNEL_NAME} not visible after POST")
        new_id = int(match[0]["id"])
        print(f"created ch{new_id} {CHANNEL_NAME} (disabled)")

        # management probe while disabled
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/test/{new_id}?model={TEST_MODEL}",
            headers=headers,
            timeout=120,
        )
        if status != 200 or not isinstance(body, dict) or not body.get("success"):
            message = body.get("message") if isinstance(body, dict) else None
            raise RuntimeError(f"management probe failed: HTTP {status} message={message!r}")
        print(f"management probe ok: {body.get('time')}s model={TEST_MODEL}")

        set_status(smoke, headers, new_id, 1)
        print(f"enabled ch{new_id}")
    else:
        new_id = int(resume["id"])
        print(f"resume: reusing existing ch{new_id} {CHANNEL_NAME}")

    # ModelRatio for both ids
    row = db_one("SELECT value FROM options WHERE key = 'ModelRatio'")
    ratios = json.loads(row[0]) if row and row[0] else {}
    changed = False
    for model in (UPSTREAM_MODEL, ALIAS_MODEL):
        if ratios.get(model) != MODEL_RATIO:
            ratios[model] = MODEL_RATIO
            changed = True
    if changed:
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/option/",
            method="PUT",
            body={"key": "ModelRatio", "value": json.dumps(ratios, separators=(",", ":"), sort_keys=True)},
            headers=headers,
            timeout=30,
        )
        if status != 200 or not isinstance(body, dict) or not body.get("success"):
            message = body.get("message") if isinstance(body, dict) else None
            raise RuntimeError(f"ModelRatio update failed: HTTP {status} message={message!r}")
    print(f"ModelRatio[{UPSTREAM_MODEL},{ALIAS_MODEL}] = {MODEL_RATIO}")

    # abilities rebuild + readback
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/fix", method="POST", body={}, headers=headers, timeout=60
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"channel fix failed: HTTP {status} message={message!r}")
    for model in (UPSTREAM_MODEL, ALIAS_MODEL):
        row_ab = wait_abilities(new_id, model)
        if not row_ab or tuple(row_ab) != ("default", 1, PRIORITY, WEIGHT):
            raise RuntimeError(f"abilities ch{new_id}/{model} = {row_ab} (absent or wrong shape after 90s)")
    ratios_rb = json.loads(db_one("SELECT value FROM options WHERE key = 'ModelRatio'")[0])
    for model in (UPSTREAM_MODEL, ALIAS_MODEL):
        if ratios_rb.get(model) != MODEL_RATIO:
            raise RuntimeError(f"ModelRatio[{model}] readback = {ratios_rb.get(model)!r}")
    print(f"verify ok: abilities ch{new_id} x2 (default,1,{PRIORITY},{WEIGHT}); ratio {MODEL_RATIO}")

    # functional: gateway chat for both ids + attribution
    gateway_key = read_gateway_key()
    for model in (UPSTREAM_MODEL, ALIAS_MODEL):
        attr, body = gateway_chat(smoke, gateway_key, model, time.time())
        if attr != new_id:
            raise RuntimeError(f"{model} attribution mismatch: logs ch{attr}, expected ch{new_id}")
        usage = body.get("usage") or {}
        print(
            f"functional OK: {model} 200 usage={usage.get('prompt_tokens')}/{usage.get('completion_tokens')} "
            f"attributed ch{attr}"
        )
    print(f"DONE: ch{new_id} {CHANNEL_NAME} live p{PRIORITY}/w{WEIGHT}; OMP entry step-5-preview-free")
    return 0


if __name__ == "__main__":
    sys.exit(main())
