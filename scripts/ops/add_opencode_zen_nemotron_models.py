#!/usr/bin/env python3
"""Add the new Zen free models nemotron-3-ultra-free and
nemotron-3.5-lightning-free to ch96 (opencode-zen-free).

Both appeared in https://opencode.ai/zen/v1/models after the ch96 onboarding
run. Verified 2026-08-22 against https://opencode.ai/zen/v1 directly with the
ch96 key (browser UA required — plain clients get Cloudflare 1010):
- nemotron-3-ultra-free: chat/completions 200, content "OK"
- nemotron-3.5-lightning-free: chat/completions 200, content "OK"
- deepseek-v4-flash-free is also back in the /v1/models list but still
  broken upstream (400 "Model is unavailable") — deliberately excluded.

What --apply does (per model, same pattern as add_opencode_ox_alpha_model.py):
- whole-DB SQLite snapshot backup
- PUT /api/channel/ with models extended (full channel object minus status;
  the fork syncs abilities on update)
- management probe of each new model while the channel stays enabled
  (429/quota answers count as pass-with-warning: auth+routing proven)
- ModelRatio=0 for each (Zen free, upstream charges nothing)
- 75s channel-cache wait, then relay probes through 127.0.0.1:3002
  /v1/chat/completions using the OMP zg-newapi token from
  ~/.omp/agent/models.yml (never printed) — proves the exact OMP call path
- readback verification: channel models, abilities rows, ModelRatio entries

Rollback on failure: PUT the original channel payload back and restore the
original ModelRatio option.

Re-running is idempotent: models already on the channel are only probed and
verified.
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

CHANNEL_NAME = "opencode-zen-free"
NEW_MODELS = ["nemotron-3-ultra-free", "nemotron-3.5-lightning-free"]
MODEL_RATIO_OPTION = "ModelRatio"
FREE_MODEL_RATIO = 0
CACHE_SYNC_SECONDS = 75
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
    return parser.parse_args()


def fetch_channel(smoke, headers: dict[str, str]) -> dict:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=200", headers=headers
    )
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"channel list failed: HTTP {status}")
    items = body.get("data") or []
    if isinstance(items, dict):
        items = items.get("items") or []
    matches = [i for i in items if isinstance(i, dict) and i.get("name") == CHANNEL_NAME]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {CHANNEL_NAME!r}, got {len(matches)}")
    return matches[0]


def put_channel(smoke, headers: dict[str, str], payload: dict) -> None:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/",
        method="PUT",
        body=payload,
        headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"channel PUT failed: HTTP {status} message={message!r}")


def management_probe(smoke, headers: dict[str, str], channel_id: int, model: str) -> str:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={model}",
        headers=headers,
        timeout=65,
    )
    if status == 200 and isinstance(body, dict) and body.get("success"):
        return "ok"
    text = json.dumps(body) if isinstance(body, (dict, list)) else str(body)
    if "FreeUsageLimitError" in text or "Rate limit exceeded" in text:
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
        f"new-api-before-zen-nemotron-{time.strftime('%Y%m%d-%H%M%S')}.db"
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


def verify(db_path: Path, channel: dict, expected_models: str) -> None:
    if channel.get("models") != expected_models:
        raise RuntimeError(
            f"readback models mismatch: {channel.get('models')!r} != {expected_models!r}"
        )
    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        ratio_row = connection.execute(
            "SELECT value FROM options WHERE key = ?", (MODEL_RATIO_OPTION,)
        ).fetchone()
        for model in NEW_MODELS:
            ability = connection.execute(
                "SELECT enabled FROM abilities WHERE channel_id = ? AND model = ?",
                (int(channel["id"]), model),
            ).fetchone()
            if ability is None or ability[0] != 1:
                raise RuntimeError(f"abilities row for {model} missing or disabled")
    if ratio_row is None:
        raise RuntimeError(f"{MODEL_RATIO_OPTION} missing on readback")
    ratios = json.loads(ratio_row[0])
    for model in NEW_MODELS:
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

    channel = fetch_channel(smoke, headers)
    channel_id = int(channel["id"])
    current_models = str(channel.get("models") or "")
    models_list = [m for m in current_models.split(",") if m]
    to_add = [m for m in NEW_MODELS if m not in models_list]
    if to_add:
        updated_models = current_models + "," + ",".join(to_add)
        print(
            f"plan: ch{channel_id} {CHANNEL_NAME} models += {','.join(to_add)}, "
            f"probe each, ModelRatio=0, relay-probe via 3002"
        )
    else:
        updated_models = current_models
        print(f"plan: both models already on ch{channel_id}; probe + verify only")
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    channel_changed = False
    ratio_changed = False
    original_payload = {k: v for k, v in channel.items() if k != "status"}
    original_ratio = get_option_db(db_path, MODEL_RATIO_OPTION)
    try:
        if to_add:
            updated_payload = dict(original_payload)
            updated_payload["models"] = updated_models
            put_channel(smoke, headers, updated_payload)
            channel_changed = True
            print(f"ch{channel_id} models updated ({len(models_list)} -> "
                  f"{len(models_list) + len(to_add)})")

        for model in NEW_MODELS:
            probe_result = management_probe(smoke, headers, channel_id, model)
            print(f"ch{channel_id} management probe {probe_result} ({model})")

        original_ratios = json.loads(original_ratio)
        missing_ratio = [
            m for m in NEW_MODELS if original_ratios.get(m) != FREE_MODEL_RATIO
        ]
        if missing_ratio:
            put_option(smoke, headers, MODEL_RATIO_OPTION,
                       merge_ratio(original_ratio, missing_ratio))
            ratio_changed = True
            print(f"ModelRatio=0 set for {','.join(missing_ratio)}")

        if to_add:
            print(f"waiting {CACHE_SYNC_SECONDS}s for channel cache sync")
            time.sleep(CACHE_SYNC_SECONDS)
        for model in NEW_MODELS:
            relay_probe(smoke, model)
            print(f"relay probe ok ({model} via 3002)")

        readback = fetch_channel(smoke, headers)
        verify(db_path, readback, updated_models)
        print(
            f"OK: ch{channel_id} serves {','.join(NEW_MODELS)}; backup={backup.name}"
        )
        return 0
    except Exception:
        if channel_changed:
            try:
                put_channel(smoke, headers, original_payload)
                print(f"rollback: ch{channel_id} models restored")
            except Exception as error:
                print(f"rollback warning: channel restore failed: {error}")
        if ratio_changed:
            try:
                put_option(smoke, headers, MODEL_RATIO_OPTION, original_ratio)
            except Exception as error:
                print(f"rollback warning: could not restore ModelRatio: {error}")
        print(f"rollback attempted; full snapshot={backup.name}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
