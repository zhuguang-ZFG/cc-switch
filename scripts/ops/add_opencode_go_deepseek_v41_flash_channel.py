#!/usr/bin/env python3
"""Onboard deepseek-v4.1-flash from the OpenCode Go plan key (zen/go endpoint).

Context (2026-10-12): the OpenCode Go account's sanctioned free selection is
``longcat-2.5-preview-free``, ``step-5-preview-free`` and ``deepseek-v4.1-flash``
(user-confirmed plan state). Two legs already serve the first two (ch133,
ch184); the Go key answers 200 for ``deepseek-v4.1-flash`` upstream
(probed live: content echo OK, id present in /v1/models with 38 catalog
entries). This adds the third Go-family channel.

Differences vs the step-5/longcat campaign scripts, both deliberate:
- No alias and no model_mapping: the Go catalog id IS the canonical gateway
  id (deepseek-v4.1-flash), already served by ch208 (p30), ch15 (p25),
  ch203 (p10). The new leg joins as the free bottom tier (p0 w2) per the
  Go-family convention, so existing higher-tier routing/quota accounting is
  untouched and Go capacity is consumed only when paid tiers fail.
- ModelRatio is NOT modified: deepseek-v4.1-flash is a shared id whose ratio
  governs paid legs too (ch203/ch208); forcing 0 would silently rewrite
  billing for non-Go traffic. Campaign-only "-free" ids got ratio 0; shared
  ids do not.

Posture: type=1, base_url + key + header_override (User-Agent +
x-opencode-session) cloned from ch133 (donor never printed, never modified),
priority 0, weight 2, auto_ban=1, group default.

Workflow contract (same as sibling scripts): dup check, whole-DB online
backup, create disabled, management probe while disabled, enable only after
probe passes, /api/channel/fix, abilities + posture readback, then a fresh
management probe through the enabled channel. Gateway relay check is
best-effort: at p0 the channel is only selected when higher tiers fail, so a
relay 200 proves the shared-id contract stays intact, not channel selection.

Usage: dry-run by default; ``--apply`` executes.
"""
from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

HERE = Path(__file__).with_name("add_opencode_go_step5preview_channel.py")
DB_PATH = Path.home() / ".new-api-local" / "new-api.db"

CHANNEL_NAME = "opencode-go-deepseek-v41-flash"
KEY_DONOR = 133  # opencode-go-longcat-2.5-preview-free: key/header/base donor
MODEL = "deepseek-v4.1-flash"
TEST_MODEL = MODEL
PRIORITY = 0
WEIGHT = 2


def load_step5():
    spec = importlib.util.spec_from_file_location("step5_helpers", HERE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load helper module: {HERE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def db_one(sql, params=()):
    with closing(sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)) as c:
        return c.execute(sql, params).fetchone()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: dry-run)")
    args = parser.parse_args()

    step5 = load_step5()
    smoke = step5.load_smoke()

    donor_row = db_one("SELECT key FROM channels WHERE id = ?", (KEY_DONOR,))
    if not donor_row or not donor_row[0]:
        raise RuntimeError(f"ch{KEY_DONOR} key not readable from DB")
    donor = db_one("SELECT base_url, header_override FROM channels WHERE id = ?", (KEY_DONOR,))
    base_url = str((donor[0] if donor else "") or "").strip()
    header_override = (donor[1] if donor else "") or ""
    if not base_url or "opencode.ai/zen/go" not in base_url:
        raise RuntimeError(f"ch{KEY_DONOR} base_url unexpected: {base_url!r}")
    if "x-opencode-session" not in str(header_override):
        raise RuntimeError(f"ch{KEY_DONOR} header_override missing session header; refusing to clone")

    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}
    channels = step5.fetch_channels(smoke, headers)
    by_name = {str(c.get("name")): c for c in channels}
    resume = by_name.get(CHANNEL_NAME)

    if resume is None:
        live = db_one("SELECT channel_id FROM abilities WHERE model = ? AND enabled = 1", (MODEL,))
        if not live:
            raise RuntimeError(f"{MODEL} unexpectedly has no enabled ability before onboarding")

    print("plan:")
    print(f"  create: {CHANNEL_NAME} models={MODEL} prio={PRIORITY} w={WEIGHT} auto_ban=1")
    print(f"  key/header_override/base_url cloned from ch{KEY_DONOR} (donor not modified)")
    print("  ModelRatio untouched (shared id; paid tiers keep billing baseline)")
    print("  functional: management probe (disabled) -> enable -> fix -> abilities/posture readback -> enabled probe")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = step5.online_backup(DB_PATH)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes)")

    if resume is None:
        payload = {
            "name": CHANNEL_NAME,
            "type": 1,
            "base_url": base_url,
            "key": donor_row[0],
            "models": MODEL,
            "group": "default",
            "header_override": header_override,
            "model_mapping": "",
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
        after = step5.fetch_channels(smoke, headers)
        match = [c for c in after if str(c.get("name")) == CHANNEL_NAME]
        if not match:
            raise RuntimeError(f"{CHANNEL_NAME} not visible after POST")
        new_id = int(match[0]["id"])
        print(f"created ch{new_id} {CHANNEL_NAME} (disabled)")

        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/test/{new_id}?model={TEST_MODEL}",
            headers=headers,
            timeout=120,
        )
        if status != 200 or not isinstance(body, dict) or not body.get("success"):
            message = body.get("message") if isinstance(body, dict) else None
            raise RuntimeError(f"disabled management probe failed: HTTP {status} message={message!r}")
        print(f"disabled probe ok: {body.get('time')}s model={TEST_MODEL}")

        step5.set_status(smoke, headers, new_id, 1)
        print(f"enabled ch{new_id}")
    else:
        new_id = int(resume["id"])
        print(f"resume: reusing existing ch{new_id} {CHANNEL_NAME}")

    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/fix", method="POST", body={}, headers=headers, timeout=60
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"channel fix failed: HTTP {status}")

    row_ab = step5.wait_abilities(new_id, MODEL)
    if not row_ab or tuple(row_ab) != ("default", 1, PRIORITY, WEIGHT):
        raise RuntimeError(f"abilities ch{new_id}/{MODEL} = {row_ab} (want default,1,{PRIORITY},{WEIGHT})")

    row = db_one(
        "SELECT status, auto_ban, models, base_url, weight, priority FROM channels WHERE id = ?",
        (new_id,),
    )
    if not row or int(row[0]) != 1 or int(row[1]) != 1 or str(row[2]) != MODEL:
        raise RuntimeError(f"channel posture readback failed: {row}")

    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/test/{new_id}?model={TEST_MODEL}",
        headers=headers,
        timeout=120,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"enabled management probe failed: HTTP {status} message={message!r}")
    print(f"enabled probe ok: {body.get('time')}s model={TEST_MODEL}")

    # Best-effort relay: p0 means this leg is only selected when higher tiers
    # fail; a 200 here proves the shared-id contract stays intact.
    try:
        gateway_key = step5.read_gateway_key()
        attr, rbody = step5.gateway_chat(smoke, gateway_key, MODEL, 0.0)
        print(f"relay {'hit ch%d' % attr if attr == new_id else 'served by higher tier'}: "
              f"model={MODEL} usage={rbody.get('usage', {}).get('total_tokens')}")
    except Exception as exc:  # noqa: BLE001 - relay is advisory, tier placement known
        print(f"relay probe skipped: {exc}")

    print(f"functional: {MODEL} live ch{new_id} p{PRIORITY}/w{WEIGHT} free bottom tier "
          f"(Go family: ch133 longcat, ch{new_id} deepseek-v4.1-flash; ch184 step-5)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
