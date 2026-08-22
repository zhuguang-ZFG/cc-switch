#!/usr/bin/env python3
"""Onboard the imagic NewAPI aggregator (https://newapi.imagic.eu.org).

User decision 2026-08-22: onboard all six probe-verified models.

Verified 2026-08-22 (direct upstream probes; browser UA REQUIRED — plain
clients get Cloudflare 1010, so the channel carries a header_override
User-Agent like the furry channel):
- grok-4.20-0309-reasoning / grok-4.20-multi-agent-0309: 200 "OK" — brand-new
  pools (grok 4.20 reasoning + multi-agent)
- grok-build-0.1: 200 "OK" — first enabled provider (ch39 is disabled)
- grok-4.6: 200 "OK" — joins the pool as backup (ch89 seeseed p0)
- muse-spark-1.2-contributor: 200 "OK" — PAID-name pool (ch48 disabled); NOT
  mapped into the free -free pool, pricing unknown
- x-preview-f: 200 "OK" — PAID-name Ox Alpha pool; not the free -free pool

Pricing unknown -> ModelRatio untouched (NewAPI default ratio applies).

Workflow contract (same as the other add_* scripts): dup check, whole-DB
snapshot backup, create disabled, management probe while disabled
(test_model=grok-4.6, fast non-reasoning), enable only after probe passes,
channel + abilities readback verify, then relay probes for every model
through 127.0.0.1:3002 with the OMP zg-newapi token (never printed).

Key comes from argv and is never printed. Re-running is verify-only and never
touches an existing channel's status or key.
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

CHANNEL_NAME = "imagic"
BASE_URL = "https://newapi.imagic.eu.org"  # type=1 自动拼 /v1，base 不带 /v1
CHANNEL_MODELS = [
    "grok-4.20-0309-reasoning",
    "grok-4.20-multi-agent-0309",
    "grok-build-0.1",
    "grok-4.6",
    "muse-spark-1.2-contributor",
    "x-preview-f",
]
MODELS = ",".join(CHANNEL_MODELS)
TEST_MODEL = "grok-4.6"  # 管理探针用快速非推理模型
PRIORITY = 0
WEIGHT = 5
CACHE_SYNC_SECONDS = 75
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HEADER_OVERRIDE = json.dumps({"User-Agent": BROWSER_UA})
OMP_MODELS_YML = Path.home() / ".omp" / "agent" / "models.yml"


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
    parser.add_argument("key", help="imagic API key (never printed)")
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
    if len(items) >= 200:
        raise RuntimeError("channel list page full (>=200); paginate before use")
    return items


def channel_payload(key: str) -> dict:
    return {
        "name": CHANNEL_NAME,
        "type": 1,  # OpenAI
        "key": key,
        "base_url": BASE_URL,
        "models": MODELS,
        "group": "default",
        "header_override": HEADER_OVERRIDE,
        "test_model": TEST_MODEL,
        "priority": PRIORITY,
        "weight": WEIGHT,
        "status": 2,  # created disabled; enabled only after probe passes
        "auto_ban": 1,
    }


def set_status(smoke, headers: dict[str, str], channel_id: int, status: int) -> None:
    response_status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}/status",
        method="POST",
        body={"status": status},
        headers=headers,
    )
    if response_status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(
            f"channel {channel_id} status={status} failed: HTTP {response_status}"
        )


def management_probe(smoke, headers: dict[str, str], channel_id: int) -> str:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={TEST_MODEL}",
        headers=headers,
        timeout=65,
    )
    if status == 200 and isinstance(body, dict) and body.get("success"):
        return "ok"
    text = json.dumps(body) if isinstance(body, (dict, list)) else str(body)
    if "429" in text or "Rate limit" in text or "usage limit" in text.lower():
        return "quota"
    message = body.get("message") if isinstance(body, dict) else None
    raise RuntimeError(
        f"management probe failed: HTTP {status} message={message!r}"
    )


def read_omp_relay_token() -> str:
    text = OMP_MODELS_YML.read_text(encoding="utf-8")
    match = re.search(r"^\s*apiKey:\s*(sk-\S+)\s*$", text, re.MULTILINE)
    if match is None:
        raise RuntimeError(f"no sk- apiKey found in {OMP_MODELS_YML}")
    return match.group(1)


def relay_probe(smoke, model: str) -> None:
    """Prove the exact OMP call path: NewAPI relay /v1/chat/completions."""
    token = read_omp_relay_token()
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/v1/chat/completions",
        method="POST",
        body={
            "model": model,
            "messages": [{"role": "user", "content": "say OK"}],
            "max_tokens": 512,  # 预防隐式推理吃光小 max_tokens 导致空 content
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=65,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("choices"):
        text = json.dumps(body) if isinstance(body, (dict, list)) else str(body)
        raise RuntimeError(f"relay probe failed for {model}: HTTP {status} {text[:200]!r}")


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        f"new-api-before-imagic-{time.strftime('%Y%m%d-%H%M%S')}.db"
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


def verify(
    db_path: Path, items: list[dict], channel_id: int,
    expected_status: int, strict: bool,
) -> None:
    channel = next((i for i in items if i.get("id") == channel_id), None)
    if channel is None:
        raise RuntimeError(f"ch{channel_id} missing on readback")
    expected = {
        "name": CHANNEL_NAME,
        "type": 1,
        "status": expected_status,
        "base_url": BASE_URL,
        "models": MODELS,
        "test_model": TEST_MODEL,
    }
    if strict:
        expected.update({"auto_ban": 1, "priority": PRIORITY, "weight": WEIGHT})
    mismatch = {
        field: (channel.get(field), value)
        for field, value in expected.items()
        if channel.get(field) != value
    }
    try:
        header_ok = json.loads(str(channel.get("header_override") or "null")) == {
            "User-Agent": BROWSER_UA
        }
    except json.JSONDecodeError:
        header_ok = False
    if not header_ok:
        mismatch["header_override"] = ("drifted", "expected")

    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        ability_enabled = 1 if expected_status == 1 else 0
        abilities_bad = []
        for model in CHANNEL_MODELS:
            ability = connection.execute(
                "SELECT enabled FROM abilities WHERE channel_id = ? AND model = ?",
                (channel_id, model),
            ).fetchone()
            if ability is None or ability[0] != ability_enabled:
                abilities_bad.append(model)
    if mismatch or abilities_bad:
        raise RuntimeError(
            f"readback mismatch for ch{channel_id}: "
            f"channel={mismatch or 'ok'} abilities_bad={abilities_bad or 'none'}"
        )


def main() -> int:
    args = parse_args()
    key = args.key.strip()
    if not key:
        raise RuntimeError("key must not be empty")

    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    token, user_id = smoke.admin_auth()
    headers = {
        "Authorization": f"Bearer {token}",
        "New-Api-User": str(user_id),
    }
    items = list_channels(smoke, headers)
    existing: dict | None = None
    named = [i for i in items if i.get("name") == CHANNEL_NAME]
    named_ids = {int(i["id"]) for i in named if isinstance(i.get("id"), int)}
    if len(named_ids) > 1:
        raise RuntimeError(f"duplicate channel name {CHANNEL_NAME!r}: {sorted(named_ids)}")
    if named_ids:
        existing = next(i for i in named if isinstance(i.get("id"), int))
    # model-name collision check: other pool members are expected
    # (grok-4.6 aggregates with ch89 by design); note them
    for i in items:
        if i.get("name") == CHANNEL_NAME:
            continue
        models = {m.strip() for m in str(i.get("models") or "").split(",")}
        shared = sorted(models & set(CHANNEL_MODELS))
        if shared:
            print(
                f"note: ch{i.get('id')} {i.get('name')} (status={i.get('status')}) "
                f"also declares {','.join(shared)} — pool will aggregate"
            )
    max_id = max(
        (int(i["id"]) for i in items if isinstance(i.get("id"), int)), default=0
    )
    planned_id = max_id + 1
    if existing is not None:
        print(f"plan: {CHANNEL_NAME} exists as ch{existing['id']}; verify only")
    else:
        print(
            f"plan: create {CHANNEL_NAME} as ch{planned_id} (key={mask(key)}) "
            f"disabled, probe ({TEST_MODEL}), enable at p{PRIORITY}/w{WEIGHT}, "
            f"relay-probe {len(CHANNEL_MODELS)} models via 3002"
        )
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    created_new = False
    try:
        if existing is not None:
            channel_id = int(existing["id"])
            probe_result = management_probe(smoke, headers, channel_id)
            print(
                f"ch{channel_id} {CHANNEL_NAME} exists "
                f"(status={existing.get('status')}); probe {probe_result}, "
                f"status untouched"
            )
        else:
            status, body = smoke.http_json(
                f"{smoke.NEWAPI_BASE}/api/channel/",
                method="POST",
                body={"mode": "single", "channel": channel_payload(key)},
                headers=headers,
            )
            if status != 200 or not isinstance(body, dict) or not body.get("success"):
                message = body.get("message") if isinstance(body, dict) else None
                raise RuntimeError(f"create failed: HTTP {status} message={message!r}")
            items = list_channels(smoke, headers)
            created = next((i for i in items if i.get("name") == CHANNEL_NAME), None)
            if created is None or not isinstance(created.get("id"), int):
                raise RuntimeError(f"created {CHANNEL_NAME} missing on readback")
            channel_id = int(created["id"])
            if channel_id != planned_id:
                set_status(smoke, headers, channel_id, 2)
                raise RuntimeError(
                    f"created unexpected channel id {channel_id}; "
                    f"expected {planned_id} (left disabled; manual "
                    f"enable or delete + re-run required)"
                )
            if created.get("status") != 2:
                set_status(smoke, headers, channel_id, 2)
            created_new = True
            print(f"ch{channel_id} {CHANNEL_NAME} created disabled")

            probe_result = management_probe(smoke, headers, channel_id)
            print(f"ch{channel_id} management probe {probe_result} ({TEST_MODEL})")

            set_status(smoke, headers, channel_id, 1)
            print(f"ch{channel_id} enabled at p{PRIORITY}/w{WEIGHT}")

            print(f"waiting {CACHE_SYNC_SECONDS}s for channel cache sync")
            time.sleep(CACHE_SYNC_SECONDS)
            for model in CHANNEL_MODELS:
                relay_probe(smoke, model)
                print(f"relay probe ok ({model} via 3002)")

        if created_new:
            items = list_channels(smoke, headers)
            verify(db_path, items, channel_id, expected_status=1, strict=True)
            print(
                f"OK: ch{channel_id} {CHANNEL_NAME} live at p{PRIORITY}/w{WEIGHT}; "
                f"backup={backup.name}"
            )
        else:
            items = list_channels(smoke, headers)
            current = next(
                (i for i in items if i.get("id") == channel_id), existing
            )
            status_value = current.get("status")
            if not isinstance(status_value, int):
                status_value = existing.get("status") if existing else None
            if not isinstance(status_value, int):
                raise RuntimeError(f"ch{channel_id} status unavailable in API projection")
            verify(db_path, items, channel_id, expected_status=status_value, strict=False)
            print(
                f"OK: ch{channel_id} {CHANNEL_NAME} present, "
                f"status={status_value} untouched; backup={backup.name}"
            )
        return 0
    except Exception:
        if created_new:
            try:
                set_status(smoke, headers, channel_id, 2)
                print(f"rollback: ch{channel_id} disabled")
            except Exception as error:
                print(f"rollback warning: could not disable ch{channel_id}: {error}")
        print(f"rollback attempted; full snapshot={backup.name}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
