#!/usr/bin/env python3
"""Sync ch111 bai-free model list with the live api.b.ai free catalog
(2026-09-04).

Background
----------
User asked to refresh the Bai free-model list: drop stale models, add new
free ones. The upstream catalog (https://api.b.ai/v1/models, 47 models)
was probed per model via chat completions with the ch111 key and browser UA:

- 200 OK: mimo-v2.5, hy3, deepseek-v4-flash, deepseek-v4-flash-vision-exp,
  glm-5.3-flash, qwen3.8-27b, qwen3.8-flash, hy4-preview, minimax-m2.7.
  (deepseek-v4-flash 429'd on the parallel batch but returned 200 on serial
  retry = transient rate limit.)
- 503 "no available channel under group mi": mimo-v2.5-pro -> STALE, drop.
- 403 "Deposit required": claude-*/gemini-*/gpt-*/kimi-*,
  deepseek-v4-pro, glm-5.1/5.2, minimax-m3, qwen3.8-max -> premium.
- 429 only (no 200 seen): glm-5.3, gpt-5.6-luna -> do NOT add blindly.

Lesson (same day): "200 OK" does NOT prove free. Two failure modes showed up
within hours, both caught by a sentinel test (hy4-preview 400s carrying the
live account balance; balance read before/after each model's request):

- Credit THRESHOLD models: hy4-preview (required=5010, balance~2600) and
  minimax-m2.7 (passed at balance~2500+, 400'd at 2362) return 200 until the
  account balance drops below the model's requirement, then 400 every time.
- Credit CONSUMING models: deepseek-v4-flash (-9 quota per small probe) and
  deepseek-v4-flash-vision-exp (-30) measurably drain the b.ai account.
  Real traffic would drain the account and then break the genuinely free
  models too (thresholds apply account-wide).

Sentinel-verified FREE (zero balance movement): mimo-v2.5, hy3.
Re-add criteria for any dropped model: b.ai recharge AND a fresh direct 200
probe AND a sentinel consumption check (balance unchanged around the probe).

Closed single-model bai channels are untouched (probed OK, freeness NOT
sentinel-verified): ch113 qwen3.8-27b, ch121 glm-5.3-flash, ch122
qwen3.8-flash.

Change
------
Final ch111 models: mimo-v2.5,hy3 (2). Removed: mimo-v2.5-pro (503 stale),
hy4-preview + minimax-m2.7 (credit thresholds), deepseek-v4-flash +
deepseek-v4-flash-vision-exp (consume credit). Priority/weight/status/key
unchanged. ModelRatio: drop pool-only keys (mimo-v2.5-pro, hy4-preview,
minimax-m2.7); deepseek-v4-flash keeps its GLOBAL 0.5 ratio (shared with
ch110/ch118); vision-exp has no ratio key. Existing ratios (hy3 0.5,
mimo-v2.5 0) untouched.

Safety: online DB snapshot first, dry-run default, API+DB readback verify,
full rollback (channel models + ModelRatio) on failure. Idempotent.
test_model is self-healed: if the current test_model is removed (or not in
the surviving list) it is re-pointed to the first surviving model in the
same PUT — an out-of-list test_model makes every Guardian error-scan /
NewAPI auto-test fail and (with auto_ban=1) risks quarantining the whole
pool. verify() asserts test_model ∈ surviving models.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")

CHANNEL_NAME = "bai-free"
# Channel-list removals (ch111 scope only).
REMOVE_MODELS = ["mimo-v2.5-pro", "hy4-preview", "minimax-m2.7",
                 "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"]
# Models whose ModelRatio was introduced for this pool and may be dropped
# globally. deepseek-v4-flash is served by ch110/ch118 too -> its 0.5 ratio
# is shared and must survive; vision-exp has no ratio key.
POOL_ONLY_RATIO_MODELS = ["mimo-v2.5-pro", "hy4-preview", "minimax-m2.7"]
ADD_MODELS: list[str] = []
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
    if "FreeUsageLimitError" in text or "Rate limit exceeded" in text or "429" in text:
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
        timeout=65,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("choices"):
        text = json.dumps(body) if isinstance(body, (dict, list)) else str(body)
        raise RuntimeError(f"relay probe failed for {model}: HTTP {status} {text[:200]!r}")


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        f"new-api-before-bai-free-sync-{time.strftime('%Y%m%d-%H%M%S')}.db"
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


def merge_ratio(current: str) -> str:
    """Set ADD_MODELS to 0 and drop POOL_ONLY_RATIO_MODELS keys.

    REMOVE_MODELS entries that are served by non-bai channels too
    (deepseek-v4-flash on ch110/ch118) keep their global ratio.
    """
    try:
        ratios = json.loads(current)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{MODEL_RATIO_OPTION} is invalid JSON") from error
    if not isinstance(ratios, dict):
        raise RuntimeError(f"{MODEL_RATIO_OPTION} must be a JSON object")
    for model in ADD_MODELS:
        ratios[model] = FREE_MODEL_RATIO
    for model in POOL_ONLY_RATIO_MODELS:
        ratios.pop(model, None)
    return json.dumps(ratios, separators=(",", ":"), sort_keys=True)


def verify(db_path: Path, channel: dict, expected_models: str) -> None:
    surviving = [m for m in expected_models.split(",") if m]
    test_model = str(channel.get("test_model") or "")
    if test_model not in surviving:
        raise RuntimeError(
            f"test_model {test_model!r} not in surviving models {surviving}"
        )
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
        for model in ADD_MODELS:
            ability = connection.execute(
                "SELECT enabled FROM abilities WHERE channel_id = ? AND model = ?",
                (int(channel["id"]), model),
            ).fetchone()
            if ability is None or ability[0] != 1:
                raise RuntimeError(f"abilities row for {model} missing or disabled")
        for model in REMOVE_MODELS:
            ability = connection.execute(
                "SELECT enabled FROM abilities WHERE channel_id = ? AND model = ?",
                (int(channel["id"]), model),
            ).fetchone()
            if ability is not None:
                raise RuntimeError(f"stale abilities row for {model} still present")
    if ratio_row is None:
        raise RuntimeError(f"{MODEL_RATIO_OPTION} missing on readback")
    ratios = json.loads(ratio_row[0])
    for model in ADD_MODELS:
        if ratios.get(model) != FREE_MODEL_RATIO:
            raise RuntimeError(f"ModelRatio for {model} != 0 on readback")
    for model in POOL_ONLY_RATIO_MODELS:
        if model in ratios:
            raise RuntimeError(f"stale ModelRatio key {model} still present")


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

    to_remove = [m for m in REMOVE_MODELS if m in models_list]
    to_add = [m for m in ADD_MODELS if m not in models_list]
    unchanged = [m for m in models_list if m not in REMOVE_MODELS]
    updated_models = ",".join(unchanged + to_add)

    print(f"ch{channel_id} {CHANNEL_NAME} ({len(models_list)} -> {len(unchanged + to_add)}):")
    if to_remove:
        print(f"  drop: {','.join(to_remove)} (stale / threshold / consumes credit)")
    if to_add:
        print(f"  add:  {','.join(to_add)} (probed 200 + sentinel-verified free)")
    for m in (m for m in unchanged if m):
        print(f"  keep: {m}")
    if not (to_remove or to_add):
        print("no model changes needed; verify only")
        if not args.apply:
            print("dry-run: no changes made")
            return 0

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
        if to_remove or to_add:
            updated_payload = dict(original_payload)
            updated_payload["models"] = updated_models
            surviving = updated_models.split(",")
            current_test_model = str(original_payload.get("test_model") or "")
            if current_test_model not in surviving:
                fallback_test_model = surviving[0]
                updated_payload["test_model"] = fallback_test_model
                print(
                    f"test_model {current_test_model!r} not in surviving models; "
                    f"set to {fallback_test_model!r}"
                )
            put_channel(smoke, headers, updated_payload)
            channel_changed = True
            print(f"ch{channel_id} models updated: {updated_models}")

        for model in ADD_MODELS:
            probe_result = management_probe(smoke, headers, channel_id, model)
            print(f"ch{channel_id} management probe {probe_result} ({model})")

        original_ratios = json.loads(original_ratio)
        ratio_needed = any(
            original_ratios.get(m) != FREE_MODEL_RATIO for m in ADD_MODELS
        ) or any(m in original_ratios for m in POOL_ONLY_RATIO_MODELS)
        if ratio_needed:
            put_option(smoke, headers, MODEL_RATIO_OPTION, merge_ratio(original_ratio))
            ratio_changed = True
            print(f"ModelRatio: {','.join(ADD_MODELS)}=0, "
                  f"{','.join(POOL_ONLY_RATIO_MODELS)} removed (pool-only; "
                  f"shared ratios untouched)")

        if to_remove or to_add:
            print(f"waiting {CACHE_SYNC_SECONDS}s for channel cache sync")
            time.sleep(CACHE_SYNC_SECONDS)
        for model in ADD_MODELS:
            relay_probe(smoke, model)
            print(f"relay probe ok ({model} via 3002)")

        readback = fetch_channel(smoke, headers)
        verify(db_path, readback, updated_models)
        print(f"OK: ch{channel_id} serves: {updated_models}; backup={backup.name}")
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