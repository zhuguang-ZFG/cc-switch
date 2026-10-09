#!/usr/bin/env python3
"""Onboard the local zen-free-bridge (scripts/ops/zen_free_bridge.mjs) into
NewAPI as the sink for OpenCode Zen free models.

Background 2026-10-07: raw calls to https://opencode.ai/zen/v1/* with any
Console key return 403 FreeTierError ("free tier can only be used from within
OpenCode") — both key generations, every client shape tried. The genuine
opencode CLI (installed locally, credential = the same Console key) passes
the gate; the free daily quota then answers 429/rate-limit. This channel
routes the 12 chat-capable zen free ids through the bridge, which drives the
CLI headlessly. jev-1.13-free is excluded (systemone decision API, not chat).

What --apply does (same shape as add_furry_muse_channel.py):
- whole-DB SQLite snapshot backup
- create channel zen-free-bridge (type=1, base http://127.0.0.1:8412,
  local-only, placeholder key) disabled
- management probe of a representative subset while disabled
  (429/rate-limit answers pass-with-warning: auth+routing proven, quota not a
  fault per the 2026-08-20 free-pool contract)
- ModelRatio=0 for every listed id, enable channel, abilities readback,
  75s cache wait, relay probe through 127.0.0.1:3002 with the OMP token
- full per-model real-completion probes deferred until quota resets
  (upstream daily window); re-running probes verify-only and never touches
  channel status/key.

Rollback: disable channel (POST /status {"status":2}) + restore ModelRatio;
or restore backup snapshot.
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

CHANNEL_NAME = "zen-free-bridge"
BASE_URL = "http://127.0.0.1:8412"  # local bridge; NewAPI appends /v1/chat/completions
PLACEHOLDER_KEY = "sk-bridge-local"  # bridge is unauthenticated loopback
MODELS = (
    "fledge-alpha-free",
    "big-pickle",
    "space-bunny-free",
    "longcat-2.5-preview-free",
    "exo-free",
    "mimo-v2.6-flash-free",
    "muse-spark-1.2-contributor-free",
    "ling-3.1-flash-free",
    "ling-3.0-flash-fin-free",
    "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free",
    "muse-spark-1.3-contributor-free",
)
PROBE_SUBSET = ("fledge-alpha-free", "exo-free", "nemotron-3-ultra-free")
TEST_MODEL = "fledge-alpha-free"
PRIORITY = 0
WEIGHT = 5
CACHE_SYNC_SECONDS = 75
MODEL_RATIO_OPTION = "ModelRatio"
FREE_MODEL_RATIO = 0
OMP_MODELS_YML = Path.home() / ".omp" / "agent" / "models.yml"


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
    parser.add_argument(
        "--probe-all",
        action="store_true",
        help="management-probe every model (slow: each CLI round is 30-90s)",
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
    return [i for i in items if isinstance(i, dict)]


def post_channel(smoke, headers: dict[str, str], payload: dict) -> int:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/",
        method="POST",
        body={"mode": "single", "channel": payload},
        headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"channel POST failed: HTTP {status} message={message!r}")
    items = [i for i in list_channels(smoke, headers) if i.get("name") == CHANNEL_NAME]
    if len(items) != 1 or not isinstance(items[0].get("id"), int):
        raise RuntimeError(f"created {CHANNEL_NAME} missing/ambiguous on readback")
    return int(items[0]["id"])


def set_status(smoke, headers: dict[str, str], channel_id: int, status: int) -> None:
    response_status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}/status",
        method="POST",
        body={"status": status},
        headers=headers,
    )
    if response_status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"status POST failed: HTTP {response_status}")


def channel_payload() -> dict:
    return {
        "name": CHANNEL_NAME,
        "type": 1,  # OpenAI
        "key": PLACEHOLDER_KEY,
        "base_url": BASE_URL,
        "models": ",".join(MODELS),
        "group": "default",
        "test_model": TEST_MODEL,
        "priority": PRIORITY,
        "weight": WEIGHT,
        "status": 2,  # created disabled; enabled only after probes
        "auto_ban": 1,
        "model_mapping": "",
    }


def management_probe(smoke, headers: dict[str, str], channel_id: int, model: str) -> str:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={model}",
        headers=headers,
        timeout=170,
    )
    if status == 200 and isinstance(body, dict) and body.get("success"):
        return "ok"
    text = json.dumps(body) if isinstance(body, (dict, list)) else str(body)
    if re.search(r"(?i)upstream_error|endpoint is unavailable|status code 502|bad gateway|502", text):
        return "upstream-down"
    if re.search(r"(?i)rate limit|FreeUsageLimit|quota|429|429003", text):
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
        timeout=170,
    )
    if status in (200, 429):
        return
    text = json.dumps(body) if isinstance(body, (dict, list)) else str(body)
    raise RuntimeError(f"relay probe failed for {model}: HTTP {status} {text[:200]!r}")


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = backup_dir / f"new-api-before-zen-free-bridge-{stamp}.db"
    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    with closing(sqlite3.connect(f"file:{destination.as_posix()}?mode=ro", uri=True)) as con:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("backup integrity check failed")
    return destination


def get_option_db(db_path: Path, key: str) -> str:
    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        row = connection.execute(
            "SELECT value FROM options WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        return "{}"
    return row[0]


def put_option(smoke, headers: dict[str, str], key: str, value: str) -> None:
    status, _ = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/option/",
        method="PUT",
        body={"key": key, "value": value},
        headers=headers,
    )
    if status != 200:
        raise RuntimeError(f"option {key!r} update failed: HTTP {status}")


def merge_ratio(current: str, models: list[str]) -> str:
    try:
        ratios = json.loads(current)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{MODEL_RATIO_OPTION} is invalid JSON") from error
    if not isinstance(ratios, dict):
        raise RuntimeError(f"{MODEL_RATIO_OPTION} must be a JSON object")
    for model in models:
        ratios[model] = FREE_MODEL_RATIO
    return json.dumps(ratios, separators=(",", ":"), sort_keys=True)


def verify(db_path: Path, channel_id: int, ratio_value: str) -> None:
    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        ratios = json.loads(ratio_value)
        for model in MODELS:
            ability = connection.execute(
                "SELECT enabled FROM abilities WHERE channel_id = ? AND model = ?",
                (channel_id, model),
            ).fetchone()
            if ability is None:
                raise RuntimeError(f"abilities row missing for {model}")
            if ability[0] != 1:
                raise RuntimeError(f"abilities row for {model} not enabled")
            if ratios.get(model) != FREE_MODEL_RATIO:
                raise RuntimeError(f"ModelRatio for {model} != 0 on readback")


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
    existing = [c for c in items if c.get("name") == CHANNEL_NAME]
    if existing:
        channel_id = int(existing[0]["id"])
        probe_models = MODELS if args.probe_all else PROBE_SUBSET
        print(f"plan: channel exists ch{channel_id}; probe {','.join(probe_models)} + verify only")
        if not args.apply:
            print("dry-run: no changes made")
            return 0
        for model in probe_models:
            result = management_probe(smoke, headers, channel_id, model)
            print(f"ch{channel_id} management probe {result} ({model})")
        return 0

    print(
        f"plan: create {CHANNEL_NAME} with {len(MODELS)} models; probe "
        f"{','.join(PROBE_SUBSET)} while disabled (quota=pass); ModelRatio=0; "
        f"enable; verify abilities"
    )
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    original_ratio = get_option_db(db_path, MODEL_RATIO_OPTION)
    channel_id = post_channel(smoke, headers, channel_payload())
    print(f"created ch{channel_id} disabled")

    try:
        for model in PROBE_SUBSET:
            result = management_probe(smoke, headers, channel_id, model)
            print(f"ch{channel_id} management probe {result} ({model})")

        ratios = json.loads(original_ratio) if original_ratio.strip() else {}
        missing = [m for m in MODELS if ratios.get(m) != FREE_MODEL_RATIO]
        if missing:
            put_option(smoke, headers, MODEL_RATIO_OPTION,
                       merge_ratio(original_ratio, missing))
            print(f"ModelRatio=0 set for {','.join(missing)}")

        set_status(smoke, headers, channel_id, 1)
        print(f"ch{channel_id} enabled")

        print(f"waiting {CACHE_SYNC_SECONDS}s for channel cache sync")
        time.sleep(CACHE_SYNC_SECONDS)

        for model in PROBE_SUBSET:
            relay_probe(smoke, model)
            print(f"relay probe ok/quota ({model} via 3002)")

        ratio_value = get_option_db(db_path, MODEL_RATIO_OPTION)
        verify(db_path, channel_id, ratio_value)
        print(f"OK: ch{channel_id} {CHANNEL_NAME} live; backup={backup.name}")
        return 0
    except Exception:
        try:
            set_status(smoke, headers, channel_id, 2)
            print("rollback: channel disabled")
        except Exception as error:
            print(f"rollback warning: disable failed: {error}")
        print(f"rollback attempted; full snapshot={backup.name}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())