#!/usr/bin/env python3
"""Onboard hashneuron.space (routeopen reseller) into local NewAPI (127.0.0.1:3002).

Upstream https://hashneuron.space/v1 is OpenAI-compatible. /v1/models declares:
composer-2.5, grok-4.5, grok-4.6, z-ai/glm-5.3-free.

2026-09-13 upstream state (probed directly):
- z-ai/glm-5.3-free: verified live; upstream reports it as `glm-5.3`
  (reasoning model). Aggregated under BOTH local names via model_mapping:
  `glm-5.3` joins the existing pool (ch45 agentrouter p40, ch121 bai p30)
  and `z-ai/glm-5.3-free` stays ch129-exclusive for free-tier pinning.
- composer-2.5 / grok-4.5 / grok-4.6: account-level daily budget exhausted
  (`daily_sdk_token_limit_exceeded`, resets 2026-09-14T00:00:00Z). Zero
  capacity until reset -> not routed yet (trio already served by ch109
  imagic + ch89; adding budget-dead models only injects 429 noise and
  risks auto-ban disabling the whole channel).

Modes (all dry-run unless --apply):
- default: create/verify the channel at the INITIAL shape (free pair),
  created disabled, probe, ModelRatio=0, enable, relay-probe via 3002.
- --map-glm: sync an existing channel to the INITIAL shape (adds the
  `glm-5.3` mapping) via the ch127 PUT contract.
- --extend-pool: sync to the full pool after the budget reset — adds
  `composer` (mapped to composer-2.5) and the grok pair, lands at backup
  tier p0/w3 beside imagic ch109.

Contract:
- whole-DB online backup before any change
- PUT contract (ch127 runbook): fork rejects minimal PUT bodies; GET masks
  the key to empty string -> rebuild the full object, drop `status`, set
  the real key explicitly; the fork syncs abilities on update
- management probes tolerate `quota` (budget not reset) — report, never gate
- rollback: restore previous models/mapping/priority/weight (sync) or
  disable created channel / restore ModelRatio (create) on failure
"""
import argparse
import importlib.util
import json
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")

CHANNEL_NAME = "hashneuron"
BASE_URL = "https://hashneuron.space"
TEST_MODEL = "z-ai/glm-5.3-free"
INITIAL_MODELS = "glm-5.3," + TEST_MODEL
INITIAL_MAPPING = {"glm-5.3": TEST_MODEL}
# full upstream pool under LOCAL names; grok names pass through unchanged
POOL_MODELS = [
    "glm-5.3",
    "z-ai/glm-5.3-free",
    "composer",
    "grok-4.5",
    "grok-4.6",
]
POOL_MAPPING = {"glm-5.3": TEST_MODEL, "composer": "composer-2.5"}
PRIORITY = 30
WEIGHT = 5
POOL_PRIORITY = 0
POOL_WEIGHT = 3  # backup tier next to ch109 imagic (p0/w5)
MODEL_RATIO_OPTION = "ModelRatio"
MODEL_RATIO = 0  # free source; marginal cost per call is zero
CACHE_SYNC_SECONDS = 75
OMP_MODELS_YML = Path.home() / ".omp" / "agent" / "models.yml"


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mask(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]} (len={len(key)})"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", required=True, help="hashneuron sk-ro-... key")
    parser.add_argument("--apply", action="store_true", help="apply changes (default dry-run)")
    parser.add_argument(
        "--extend-pool", action="store_true",
        help="extend the existing channel models to the full upstream pool "
             "(run after the account budget resets)",
    )
    parser.add_argument(
        "--map-glm", action="store_true",
        help="sync the channel to the INITIAL shape: glm-5.3 + "
             "z-ai/glm-5.3-free with model_mapping glm-5.3 -> z-ai/glm-5.3-free",
    )
    return parser.parse_args()


def list_channels(smoke, headers: dict[str, str]) -> list[dict]:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/?p=0&page_size=200", headers=headers
    )
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"channel list failed: HTTP {status}")
    items = (body.get("data") or {}).get("items") or body.get("data") or []
    if not isinstance(items, list):
        raise RuntimeError(f"channel list malformed: HTTP {status}")
    return items


def channel_payload(key: str) -> dict:
    return {
        "name": CHANNEL_NAME,
        "type": 1,  # OpenAI
        "key": key,
        "base_url": BASE_URL,
        "models": INITIAL_MODELS,
        "group": "default",
        "test_model": TEST_MODEL,
        "model_mapping": json.dumps(INITIAL_MAPPING),
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


def management_probe(smoke, headers: dict[str, str], channel_id: int, model: str) -> str:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={model}",
        headers=headers,
        timeout=65,
    )
    if status == 200 and isinstance(body, dict) and body.get("success"):
        return "ok"
    text = json.dumps(body) if isinstance(body, (dict, list)) else str(body)
    if "budget" in text.lower() or "429" in text or "rate limit" in text.lower():
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


def relay_probe(smoke, model: str = TEST_MODEL) -> None:
    """Prove the exact client call path: NewAPI relay /v1/chat/completions."""
    token = read_omp_relay_token()
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/v1/chat/completions",
        method="POST",
        body={
            "model": model,
            "messages": [{"role": "user", "content": "say OK"}],
            "max_tokens": 1024,  # glm-5.3 is a reasoning model; leave headroom
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=65,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("choices"):
        text = json.dumps(body) if isinstance(body, (dict, list)) else str(body)
        raise RuntimeError(f"relay probe failed: HTTP {status} {text[:200]!r}")


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        f"new-api-before-hashneuron-{time.strftime('%Y%m%d-%H%M%S')}.db"
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


def ensure_ratio(current: str) -> str:
    try:
        ratios = json.loads(current)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{MODEL_RATIO_OPTION} is invalid JSON") from error
    if not isinstance(ratios, dict):
        raise RuntimeError(f"{MODEL_RATIO_OPTION} must be a JSON object")
    ratios[TEST_MODEL] = MODEL_RATIO
    return json.dumps(ratios, separators=(",", ":"), sort_keys=True)


def verify(
    db_path: Path, items: list[dict], channel_id: int,
    expected_status: int,
    models_csv: str, mapping: dict, priority: int, weight: int,
) -> None:
    channel = next((i for i in items if i.get("id") == channel_id), None)
    if channel is None:
        raise RuntimeError(f"ch{channel_id} missing on readback")
    mismatch: dict[str, tuple] = {}
    expected = {
        "name": CHANNEL_NAME,
        "type": 1,
        "status": expected_status,
        "base_url": BASE_URL,
        "test_model": TEST_MODEL,
        "auto_ban": 1,
        "priority": priority,
        "weight": weight,
    }
    mismatch.update({
        field: (channel.get(field), value)
        for field, value in expected.items()
        if channel.get(field) != value
    })
    got_models = {m.strip() for m in str(channel.get("models") or "").split(",")}
    want_models = {m.strip() for m in models_csv.split(",")}
    if got_models != want_models:
        mismatch["models"] = (channel.get("models"), models_csv)
    try:
        mapping_ok = json.loads(
            str(channel.get("model_mapping") or "null")
        ) == mapping
    except json.JSONDecodeError:
        mapping_ok = False
    if not mapping_ok:
        mismatch["model_mapping"] = (channel.get("model_mapping"), mapping)

    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        ability_rows = connection.execute(
            "SELECT model, enabled FROM abilities WHERE channel_id = ?",
            (channel_id,),
        ).fetchall()
        ratio_row = connection.execute(
            "SELECT value FROM options WHERE key = ?", (MODEL_RATIO_OPTION,)
        ).fetchone()
    got_abilities = {r[0]: r[1] for r in ability_rows}
    ability_enabled = 1 if expected_status == 1 else 0
    abilities_ok = all(
        got_abilities.get(m.strip()) == ability_enabled
        for m in models_csv.split(",")
    )
    ratio_ok = False
    if ratio_row is not None and isinstance(ratio_row[0], str):
        try:
            ratio_ok = json.loads(ratio_row[0]).get(TEST_MODEL) == MODEL_RATIO
        except json.JSONDecodeError:
            ratio_ok = False
    if mismatch or not abilities_ok or not ratio_ok:
        raise RuntimeError(
            f"readback mismatch for ch{channel_id}: "
            f"channel={mismatch or 'ok'} abilities_ok={abilities_ok} "
            f"ratio_ok={ratio_ok}"
        )


def create_flow(smoke, headers: dict[str, str], key: str, db_path: Path) -> tuple[int, Path]:
    """Create the channel disabled, probe, set ratio, enable, relay-probe."""
    items = list_channels(smoke, headers)
    named = [i for i in items if i.get("name") == CHANNEL_NAME]
    named_ids = {int(i["id"]) for i in named if isinstance(i.get("id"), int)}
    if len(named_ids) > 1:
        raise RuntimeError(f"duplicate channel name {CHANNEL_NAME!r}: {sorted(named_ids)}")
    existing = next((i for i in named if isinstance(i.get("id"), int)), None)
    for i in items:
        models = [m.strip() for m in str(i.get("models") or "").split(",")]
        overlap = [m for m in POOL_MODELS if m in models]
        if overlap:
            print(
                f"note: {','.join(overlap)} also declared by ch{i.get('id')} "
                f"{i.get('name')} (status={i.get('status')}) — pool will aggregate"
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
            f"disabled, probe ({TEST_MODEL}), ModelRatio=0, enable at "
            f"p{PRIORITY}/w{WEIGHT}, relay-probe via 3002"
        )
    if not _apply:
        print("dry-run: no changes made")
        return -1, Path("dry-run")

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    created_new = False
    ratio_changed = False
    original_ratio = get_option_db(db_path, MODEL_RATIO_OPTION)
    try:
        if existing is not None:
            channel_id = int(existing["id"])
            probe_result = management_probe(smoke, headers, channel_id, TEST_MODEL)
            print(
                f"ch{channel_id} {CHANNEL_NAME} exists "
                f"(status={existing.get('status')}); probe {probe_result}, "
                f"status untouched"
            )
            try:
                mapped_result = management_probe(smoke, headers, channel_id, "glm-5.3")
            except RuntimeError as error:
                mapped_result = f"error: {error}"
            print(f"ch{channel_id} management probe {mapped_result} (glm-5.3 mapped)")
            if json.loads(original_ratio).get(TEST_MODEL) != MODEL_RATIO:
                put_option(smoke, headers, MODEL_RATIO_OPTION,
                           ensure_ratio(original_ratio))
                ratio_changed = True
                print(f"ModelRatio corrected to 0 for {TEST_MODEL}")
            channel_id_final = channel_id
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

            probe_result = management_probe(smoke, headers, channel_id, TEST_MODEL)
            print(f"ch{channel_id} management probe {probe_result} ({TEST_MODEL})")
            try:
                mapped_result = management_probe(smoke, headers, channel_id, "glm-5.3")
            except RuntimeError as error:
                mapped_result = f"error: {error}"
            print(f"ch{channel_id} management probe {mapped_result} (glm-5.3 mapped)")

            if json.loads(original_ratio).get(TEST_MODEL) != MODEL_RATIO:
                put_option(smoke, headers, MODEL_RATIO_OPTION,
                           ensure_ratio(original_ratio))
                ratio_changed = True
                print(f"ModelRatio=0 set for {TEST_MODEL} (free source)")
            else:
                print(f"ModelRatio=0 for {TEST_MODEL} already present")

            set_status(smoke, headers, channel_id, 1)
            print(f"ch{channel_id} enabled at p{PRIORITY}/w{WEIGHT}")

            print(f"waiting {CACHE_SYNC_SECONDS}s for channel cache sync")
            time.sleep(CACHE_SYNC_SECONDS)
            relay_probe(smoke)
            print(f"relay probe ok ({TEST_MODEL} via 3002)")
            channel_id_final = channel_id

        if created_new:
            items = list_channels(smoke, headers)
            verify(db_path, items, channel_id_final, 1,
                   INITIAL_MODELS, INITIAL_MAPPING, PRIORITY, WEIGHT)
            print(
                f"OK: ch{channel_id_final} {CHANNEL_NAME} live at "
                f"p{PRIORITY}/w{WEIGHT}; backup={backup.name}"
            )
        else:
            items = list_channels(smoke, headers)
            current = next(
                (i for i in items if i.get("id") == channel_id_final), existing
            )
            status_value = current.get("status") if current else None
            if not isinstance(status_value, int):
                status_value = existing.get("status") if existing else None
            if not isinstance(status_value, int):
                raise RuntimeError("ch status unavailable in API projection")
            verify(db_path, items, channel_id_final, status_value,
                   INITIAL_MODELS, INITIAL_MAPPING, PRIORITY, WEIGHT)
            print(
                f"OK: ch{channel_id_final} {CHANNEL_NAME} present, "
                f"status={status_value} untouched; backup={backup.name}"
            )
        return channel_id_final, backup
    except Exception:
        if created_new:
            try:
                set_status(smoke, headers, channel_id, 2)
                print(f"rollback: ch{channel_id} disabled")
            except Exception as error:
                print(f"rollback warning: could not disable ch{channel_id}: {error}")
        if ratio_changed:
            try:
                put_option(smoke, headers, MODEL_RATIO_OPTION, original_ratio)
            except Exception as error:
                print(f"rollback warning: could not restore ModelRatio: {error}")
        print(f"rollback attempted; full snapshot={backup.name}")
        raise


_apply = False


def put_channel_retry(
    smoke, headers: dict[str, str], payload: dict, label: str,
    attempts: int = 4, delay: int = 8,
) -> None:
    """PUT /api/channel/ with SQLITE_BUSY retry.

    The fork returns HTTP 200 + success=false on transient SQLITE_BUSY,
    and a partially committed update (models row written, abilities sync
    aborted) is possible — callers MUST verify abilities after any BUSY.
    """
    last = "no attempts made"
    for attempt in range(1, attempts + 1):
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/",
            method="PUT", body=payload, headers=headers, timeout=65,
        )
        if status == 200 and isinstance(body, dict) and body.get("success"):
            return
        message = body.get("message") if isinstance(body, dict) else str(body)
        last = f"HTTP {status} message={message!r}"
        text = str(message).lower()
        if "locked" in text or "busy" in text:
            print(f"{label} PUT busy (attempt {attempt}/{attempts}), retry in {delay}s")
            time.sleep(delay)
            continue
        break
    raise RuntimeError(f"{label} PUT failed: {last}")


def sync_flow(
    smoke, headers: dict[str, str], key: str, db_path: Path,
    label: str, models_csv: str, mapping: dict,
    priority: int, weight: int,
) -> None:
    """Sync the channel to a target model set via the ch127 PUT contract.

    fork rejects minimal PUT bodies and GET masks the key to empty string:
    rebuild the full object from the API readback, drop `status`, set the
    real key explicitly; the fork syncs abilities on update. Probes
    tolerate `quota` (budget not reset) — reported, never a gate.
    """
    items = list_channels(smoke, headers)
    named = [i for i in items if i.get("name") == CHANNEL_NAME]
    named_ids = {int(i["id"]) for i in named if isinstance(i.get("id"), int)}
    if len(named_ids) != 1:
        raise RuntimeError(
            f"expected exactly one {CHANNEL_NAME!r} channel, found {sorted(named_ids)}"
        )
    channel = next(i for i in named if isinstance(i.get("id"), int))
    channel_id = int(channel["id"])
    current_models = sorted(
        m.strip() for m in str(channel.get("models") or "").split(",")
    )
    target_models = sorted(m.strip() for m in models_csv.split(","))
    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        got_abilities = {
            r[0]: r[1] for r in connection.execute(
                "SELECT model, enabled FROM abilities WHERE channel_id = ?",
                (channel_id,),
            ).fetchall()
        }
    want_enabled = 1 if channel.get("status") == 1 else 0
    abilities_complete = all(
        got_abilities.get(m.strip()) == want_enabled
        for m in models_csv.split(",")
    )
    try:
        current_mapping = json.loads(str(channel.get("model_mapping") or "null"))
    except json.JSONDecodeError:
        current_mapping = None
    shape_ok = (
        current_models == target_models
        and current_mapping == mapping
        and abilities_complete
    )
    if shape_ok:
        print(f"ch{channel_id} already matches {label}; nothing to do")
        return
    if not _apply:
        print(
            f"dry-run: would PUT {label} models={models_csv} "
            f"mapping={json.dumps(mapping, sort_keys=True)} "
            f"p{priority}/w{weight} (keep status={channel.get('status')}); "
            f"abilities={got_abilities}"
        )
        return
    backup = online_backup(db_path)
    time.sleep(2)  # let the backup's read lock fully release before writing
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")
    previous = {
        "models": channel.get("models"),
        "model_mapping": channel.get("model_mapping"),
        "priority": channel.get("priority"),
        "weight": channel.get("weight"),
    }
    payload = {
        "id": channel_id,
        "name": CHANNEL_NAME,
        "type": 1,
        "key": key,  # GET masks the key to empty string; set the real one
        "base_url": BASE_URL,
        "models": models_csv,
        "group": channel.get("group") or "default",
        "test_model": TEST_MODEL,
        "model_mapping": json.dumps(mapping, separators=(",", ":"), sort_keys=True),
        "priority": priority,
        "weight": weight,
        "auto_ban": 1,
        # no status field: keep current status untouched
    }
    try:
        put_channel_retry(smoke, headers, payload, label)
        print(f"ch{channel_id} PUT {label} models -> {models_csv}")
        for model in models_csv.split(","):
            try:
                result = management_probe(smoke, headers, channel_id, model)
            except RuntimeError as error:
                result = f"error: {error}"
            print(f"probe {model}: {result}")
        print(f"waiting {CACHE_SYNC_SECONDS}s for channel cache sync")
        time.sleep(CACHE_SYNC_SECONDS)
        # Relay probe only for the ch129-exclusive name: mapped `glm-5.3`
        # routes the shared pool and cannot pin ch129 — the per-channel
        # management probes above already prove the upstream hop.
        if channel.get("status") == 1:
            relay_probe(smoke)
            print(f"relay probe ok ({TEST_MODEL} via 3002)")
        items = list_channels(smoke, headers)
        verify(db_path, items, channel_id, channel.get("status", 1),
               models_csv, mapping, priority, weight)
        print(
            f"OK: ch{channel_id} synced to {label} "
            f"(models={models_csv}, p{priority}/w{weight}); backup={backup.name}"
        )
    except Exception:
        try:
            restore = dict(payload)
            restore["models"] = previous["models"] or INITIAL_MODELS
            restore["model_mapping"] = previous["model_mapping"] or "{}"
            if previous["priority"] is not None:
                restore["priority"] = previous["priority"]
            if previous["weight"] is not None:
                restore["weight"] = previous["weight"]
            put_channel_retry(smoke, headers, restore, f"{label}-rollback", attempts=3)
            print(f"rollback: ch{channel_id} restored (models={restore['models']})")
        except Exception as error:
            print(f"rollback warning: could not restore ch{channel_id}: {error}")
        print(f"full snapshot={backup.name}")
        raise


def main() -> int:
    global _apply
    args = parse_args()
    key = args.key.strip()
    if not key:
        raise RuntimeError("key must not be empty")
    _apply = args.apply

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {
        "Authorization": f"Bearer {token}",
        "New-Api-User": str(user_id),
    }
    db_path = Path(smoke.NEWAPI_DB).resolve()
    if args.map_glm:
        sync_flow(smoke, headers, key, db_path, "map-glm",
                  INITIAL_MODELS, INITIAL_MAPPING, PRIORITY, WEIGHT)
        return 0
    if args.extend_pool:
        sync_flow(smoke, headers, key, db_path, "extend-pool",
                  ",".join(POOL_MODELS), POOL_MAPPING, POOL_PRIORITY, POOL_WEIGHT)
        return 0
    create_flow(smoke, headers, key, db_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
