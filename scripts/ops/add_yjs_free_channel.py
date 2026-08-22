#!/usr/bin/env python3
"""Onboard api.yjs.im (JasperAPI) Free-group key as channel `yjs-free`.

Site layout (verified 2026-08-22 via /api/pricing + /api/notice):
- NewAPI fork with group-gated catalog. Free group (group_ratio 0) holds 20
  models; unlimited group is 5k-context small models; gpt-5.6-sol/gpt-5.x live
  in paid Codex-* account pools; grok-4.5/4.6 in paid Grok-Super.
- The first key the user got was bound to Free-Lite, whose channel set is
  currently EMPTY (every model 503s "under group Free-Lite"). This key is a
  Free-group token: deepseek-v4-flash 200, gpt-5.6-sol 503 "under group Free"
  (proves group=Free and sol not in it).

Probe results with this key (2026-08-22, direct, browser UA):
- 19 models 200 OK (some reasoning models return empty content at
  max_tokens=16 — fine, HTTP 200 is the gate).
- big-pickle / mimo-v2.5: persistent upstream 429 across retries — excluded
  (both already pooled via zen ch96 anyway; revisit if yjs's upstream cools).
- gemma-4-31b-it: read timeout x2 — excluded.

Aggregation plan (single channel p6/w5, auto_ban=1, browser-UA header):
- Join existing FREE pools via mapping:
  x-preview-f-free->ox-alpha (pool: zen p10 / 168661 p7 / OR p5 / go p4),
  muse-spark-1.2-contributor-free->muse-spark-1.2-contributor (zen p10 /
  furry p9), hy3-free->hy3 (zen p10).
- Join existing PAID pools as free backup (ratio untouched; over-reporting
  cost on yjs-served calls is the conservative direction):
  k3->kimi-k3 (official p50 / whyyin p49), glm-5.2, deepseek-v4-flash,
  deepseek-v4-flash-0731, agnes-2.0-flash, agnes-2.5-flash.
- New yjs-only pools (ModelRatio += 0 for each): dots-3-note-preview,
  inkling, sensenova-6.8-flash-lite, step-3.7-flash,
  diffusiongemma-26b-a4b-it, gpt-oss-20b, glm-4.5-flash, minimax-m3,
  nemotron-3-ultra-550b-a55b, deepseek-v4-flash-preview.

Workflow contract: same as add_ai168661_ox_alpha_channel.py — dup check,
whole-DB snapshot backup, create disabled, management probe per model while
disabled (429/quota counts as pass-with-warning: auth+routing proven),
enable, ModelRatio merge for the 10 NEW pool names only, 75s cache sync,
relay probe through 127.0.0.1:3002 with the OMP zg-newapi token (never
printed), readback verify (channel fields, abilities rows, ratio entries).
Key from argv, never printed. Re-running is verify-only, never touches
status or key.
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

CHANNEL_NAME = "yjs-free"
BASE_URL = "https://api.yjs.im"  # NewAPI appends /v1/chat/completions
PRIORITY = 6
WEIGHT = 5
CACHE_SYNC_SECONDS = 75
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HEADER_OVERRIDE = json.dumps({"User-Agent": BROWSER_UA})
MODEL_RATIO_OPTION = "ModelRatio"
FREE_MODEL_RATIO = 0
OMP_MODELS_YML = Path.home() / ".omp" / "agent" / "models.yml"

# public name -> upstream name (only entries needing a mapping)
MAPPED_MODELS = {
    "x-preview-f-free": "ox-alpha",
    "muse-spark-1.2-contributor-free": "muse-spark-1.2-contributor",
    "hy3-free": "hy3",
    "k3": "kimi-k3",
}
# public names joining existing pools without a mapping (ratio untouched)
DIRECT_EXISTING_POOLS = [
    "glm-5.2",
    "deepseek-v4-flash",
    "deepseek-v4-flash-0731",
    "agnes-2.0-flash",
    "agnes-2.5-flash",
]
# new yjs-only pools; each gets ModelRatio=0
NEW_POOLS = [
    "dots-3-note-preview",
    "inkling",
    "sensenova-6.8-flash-lite",
    "step-3.7-flash",
    "diffusiongemma-26b-a4b-it",
    "gpt-oss-20b",
    "glm-4.5-flash",
    "minimax-m3",
    "nemotron-3-ultra-550b-a55b",
    "deepseek-v4-flash-preview",
]
ALL_MODELS = sorted(
    list(MAPPED_MODELS) + DIRECT_EXISTING_POOLS + NEW_POOLS
)
MODELS = ",".join(ALL_MODELS)
MODEL_MAPPING = json.dumps(MAPPED_MODELS)
TEST_MODEL = "deepseek-v4-flash"  # direct name, no mapping needed
# Relay-probe a few representative paths after cache sync.
RELAY_PROBE_MODELS = ["deepseek-v4-flash", "hy3-free", "dots-3-note-preview"]


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
    parser.add_argument("key", help="api.yjs.im Free-group key (never printed)")
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
        "model_mapping": MODEL_MAPPING,
        "group": "default",
        "header_override": HEADER_OVERRIDE,
        "test_model": TEST_MODEL,
        "priority": PRIORITY,
        "weight": WEIGHT,
        "status": 2,  # created disabled; enabled only after probes pass
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


def management_probe(smoke, headers: dict[str, str], channel_id: int, model: str) -> str:
    """Probe one public model while disabled; exercises the model_mapping."""
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={model}",
        headers=headers,
        timeout=100,
    )
    if status == 200 and isinstance(body, dict) and body.get("success"):
        return "ok"
    text = json.dumps(body) if isinstance(body, (dict, list)) else str(body)
    if "429" in text or "Rate limit" in text:
        return "quota"
    message = body.get("message") if isinstance(body, dict) else None
    raise RuntimeError(
        f"management probe failed for {model}: HTTP {status} message={message!r}"
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
            "max_tokens": 512,
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
        f"new-api-before-yjs-free-{time.strftime('%Y%m%d-%H%M%S')}.db"
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


def get_option_db(db_path: Path, key: str) -> str:
    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        row = connection.execute(
            "SELECT value FROM options WHERE key = ?", (key,)
        ).fetchone()
    if row is None or not isinstance(row[0], str):
        raise RuntimeError(f"option {key!r} is missing")
    return row[0]


def put_option(smoke, headers: dict[str, str], key: str, value: str) -> None:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/option/",
        method="PUT",
        body={"key": key, "value": value},
        headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"option {key!r} PUT failed: HTTP {status} message={message!r}")


def merge_ratio(current: str) -> str:
    try:
        ratios = json.loads(current)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{MODEL_RATIO_OPTION} is invalid JSON") from error
    if not isinstance(ratios, dict):
        raise RuntimeError(f"{MODEL_RATIO_OPTION} must be a JSON object")
    for model in NEW_POOLS:
        ratios[model] = FREE_MODEL_RATIO
    return json.dumps(ratios)


def verify(
    db_path: Path, items: list[dict], channel_id: int, expected_status: int
) -> None:
    channel = next((i for i in items if i.get("id") == channel_id), None)
    if channel is None:
        raise RuntimeError(f"ch{channel_id} missing on readback")
    expected = {
        "name": CHANNEL_NAME,
        "type": 1,
        "status": expected_status,
        "base_url": BASE_URL,
        "test_model": TEST_MODEL,
        "auto_ban": 1,
        "priority": PRIORITY,
        "weight": WEIGHT,
    }
    mismatch = {
        field: (channel.get(field), value)
        for field, value in expected.items()
        if channel.get(field) != value
    }
    live_models = sorted(str(channel.get("models") or "").split(","))
    if live_models != ALL_MODELS:
        mismatch["models"] = ("drifted", f"{len(live_models)} entries")
    try:
        mapping = json.loads(str(channel.get("model_mapping") or "null"))
    except json.JSONDecodeError:
        mapping = None
    if mapping != MAPPED_MODELS:
        mismatch["model_mapping"] = ("drifted", MODEL_MAPPING)
    try:
        header_ok = json.loads(str(channel.get("header_override") or "null")) == {
            "User-Agent": BROWSER_UA
        }
    except json.JSONDecodeError:
        header_ok = False
    if not header_ok:
        mismatch["header_override"] = ("drifted", "expected")

    ability_expected = 1 if expected_status == 1 else 0
    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        rows = dict(connection.execute(
            "SELECT model, enabled FROM abilities WHERE channel_id = ?",
            (channel_id,),
        ).fetchall())
        ratio_row = connection.execute(
            "SELECT value FROM options WHERE key = ?", (MODEL_RATIO_OPTION,)
        ).fetchone()
    bad_abilities = [
        m for m in ALL_MODELS if rows.get(m) != ability_expected
    ]
    if bad_abilities:
        mismatch["abilities"] = ("bad", bad_abilities)
    ratio_ok = False
    if ratio_row is not None and isinstance(ratio_row[0], str):
        try:
            ratios = json.loads(ratio_row[0])
            ratio_ok = all(
                ratios.get(m) == FREE_MODEL_RATIO for m in NEW_POOLS
            )
        except json.JSONDecodeError:
            ratio_ok = False
    if not ratio_ok:
        mismatch["model_ratio"] = ("missing/nonzero", NEW_POOLS)
    if mismatch:
        raise RuntimeError(f"readback mismatch for ch{channel_id}: {mismatch}")


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
    max_id = max(
        (int(i["id"]) for i in items if isinstance(i.get("id"), int)), default=0
    )
    planned_id = max_id + 1
    if existing is not None:
        print(f"plan: {CHANNEL_NAME} exists as ch{existing['id']}; verify only")
    else:
        print(
            f"plan: create {CHANNEL_NAME} as ch{planned_id} (key={mask(key)}) "
            f"disabled, probe {len(ALL_MODELS)} models, enable at "
            f"p{PRIORITY}/w{WEIGHT}, ModelRatio+=0 for {len(NEW_POOLS)} new pools, "
            f"relay-probe via 3002"
        )
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    original_ratio = get_option_db(db_path, MODEL_RATIO_OPTION)
    created_new = False
    try:
        if existing is not None:
            channel_id = int(existing["id"])
            print(
                f"ch{channel_id} {CHANNEL_NAME} exists "
                f"(status={existing.get('status')}); verify only, "
                f"status/key untouched"
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

            warnings: list[str] = []
            for model in ALL_MODELS:
                result = management_probe(smoke, headers, channel_id, model)
                if result != "ok":
                    warnings.append(f"{model}={result}")
                print(f"  probe {model}: {result}")
            if warnings:
                print(f"probe warnings (quota/rate-limit, accepted): {warnings}")

            set_status(smoke, headers, channel_id, 1)
            print(f"ch{channel_id} enabled at p{PRIORITY}/w{WEIGHT}")

            put_option(smoke, headers, MODEL_RATIO_OPTION, merge_ratio(original_ratio))
            print(f"ModelRatio: +{len(NEW_POOLS)} new pools at 0")

            print(f"waiting {CACHE_SYNC_SECONDS}s for channel cache sync")
            time.sleep(CACHE_SYNC_SECONDS)
            for model in RELAY_PROBE_MODELS:
                relay_probe(smoke, model)
                print(f"relay probe ok ({model} via 3002)")

        items = list_channels(smoke, headers)
        if created_new:
            verify(db_path, items, channel_id, expected_status=1)
        else:
            current = next(
                (i for i in items if i.get("id") == channel_id), existing
            )
            status_value = current.get("status")
            if not isinstance(status_value, int):
                raise RuntimeError(f"ch{channel_id} status unavailable in API projection")
            verify(db_path, items, channel_id, expected_status=status_value)
        print(
            f"OK: ch{channel_id} {CHANNEL_NAME} verified "
            f"({len(ALL_MODELS)} models); backup={backup.name}"
        )
        return 0
    except Exception:
        if created_new:
            try:
                set_status(smoke, headers, channel_id, 2)
                print(f"rollback: ch{channel_id} disabled")
            except Exception as error:
                print(f"rollback warning: could not disable ch{channel_id}: {error}")
            try:
                put_option(smoke, headers, MODEL_RATIO_OPTION, original_ratio)
                print("rollback: ModelRatio restored")
            except Exception as error:
                print(f"rollback warning: could not restore ModelRatio: {error}")
        print(f"rollback attempted; full snapshot={backup.name}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
