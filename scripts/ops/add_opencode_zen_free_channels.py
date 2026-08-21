#!/usr/bin/env python3
"""Onboard the OpenCode Zen free models into local NewAPI as one channel.

The OpenCode Go key (same key as ch48 opencode-go-muse) also works on the Zen
free pool (verified 2026-08-20 against https://opencode.ai/zen/v1 directly):

  chat/completions:
    hy3-free                    200
    laguna-s-2.1-free           200
    big-pickle                  429 FreeUsageLimitError (auth ok, quota out)
    mimo-v2.5-free              429 FreeUsageLimitError (auth ok, quota out)
  responses:
    muse-spark-1.2-contributor-free  200

Later pool changes (handled by dedicated scripts, kept here so MODELS stays
the live contract):
- 2026-08-21 x-preview-f-free (Ox Alpha Free) added —
  add_opencode_ox_alpha_model.py; zero-retention provider, no data gate.
- 2026-08-21 deepseek-v4-flash-free removed — its free promotion ended
  (upstream 401 ModelError), see remove_opencode_deepseek_flash_free.py.

A 429 FreeUsageLimitError proves auth + routing and only means the free daily
quota is temporarily exhausted, so the management probe accepts it as a
pass-with-warning (the channel exists precisely for when quota resets).

OMP calls the muse contributor model natively as openai-responses (see
cutover_opencode_go_muse.mjs), so NO chat-to-responses policy entry is needed;
after enabling, this script proves the exact OMP call path with a relay probe
through 127.0.0.1:3002/v1/responses using the zg-newapi token read from
~/.omp/agent/models.yml (never printed).

The two nemotron-*-free models are deliberately excluded: they are NVIDIA
trial models with stricter terms.

Zen sits behind Cloudflare like Go/justwoker/gorouter, so the channel carries
a browser-UA header_override. Posture: priority 10 / weight 5, a best-effort
free fallback pool (the models are unique to this channel, so the posture only
documents intent). auto_ban=1.

Billing truthfulness: the onboarded model names are Zen-exclusive and
cost nothing upstream, so the script merges ModelRatio=0 entries for them
(the option is backed up and restored on rollback).

Workflow contract (same as add_justwoker_opus_channel.py):
- dup check by name and (base_url, models) before creating
- whole-DB SQLite snapshot backup before any change
- POST /api/channel/ with {"mode":"single","channel":payload} double wrap
- create disabled (status=2), management probe while disabled, enable only
  after the probe passes
- channel + abilities + ModelRatio readback verification after apply

Run without --apply for a read-only plan. The key comes from argv and is never
printed (masked to first/last 4 chars). Re-running with the channel already
present only probes and verifies it — it never creates duplicates, never
changes an existing channel's status, and never updates its key.

--accept-zen-free-data-policy is required for --apply: the muse contributor
line and the Zen free tier share usage data with the upstream (same consent
gate as cutover_opencode_go_muse.mjs).
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
BASE_URL = "https://opencode.ai/zen"  # NewAPI appends /v1/chat/completions
MODELS = (
    "big-pickle,mimo-v2.5-free,hy3-free,"
    "laguna-s-2.1-free,"
    "muse-spark-1.2-contributor-free,x-preview-f-free"
)
TEST_MODEL = "hy3-free"  # the chat model verified 200 during onboarding
MUSE_FREE_MODEL = "muse-spark-1.2-contributor-free"
PRIORITY = 10
WEIGHT = 5
CACHE_SYNC_SECONDS = 75
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HEADER_OVERRIDE = json.dumps({"User-Agent": BROWSER_UA})
MODEL_RATIO_OPTION = "ModelRatio"
FREE_MODEL_RATIO = 0  # Zen free pool: upstream charges nothing
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
    parser.add_argument("key", help="OpenCode Go/Zen API key (never printed)")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the backed-up live change; default is read-only",
    )
    parser.add_argument(
        "--accept-zen-free-data-policy",
        action="store_true",
        help="acknowledge the Zen free tier / muse contributor data policy",
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
        # Single-page fetch: planned ids and readbacks silently corrupt past
        # the page boundary. Fail fast instead of paginating (one-shot tool).
        raise RuntimeError("channel list page full (>=200); paginate before use")
    return items


def channel_payload(key: str) -> dict:
    return {
        "name": CHANNEL_NAME,
        "type": 1,  # OpenAI (/v1/chat/completions; /v1/responses passthrough)
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
    """Probe the channel while disabled.

    Returns "ok" on a clean pass or "quota" when the upstream answers 429
    FreeUsageLimitError — that still proves auth + routing for a free pool
    whose daily quota comes and goes.
    """
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={TEST_MODEL}",
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
        f"management probe failed: HTTP {status} message={message!r}"
    )


def read_omp_relay_token() -> str:
    """First apiKey in ~/.omp/agent/models.yml is the zg-newapi provider key."""
    text = OMP_MODELS_YML.read_text(encoding="utf-8")
    match = re.search(r"^\s*apiKey:\s*(sk-\S+)\s*$", text, re.MULTILINE)
    if match is None:
        raise RuntimeError(f"no sk- apiKey found in {OMP_MODELS_YML}")
    return match.group(1)


def relay_responses_probe(smoke) -> None:
    """Prove the exact OMP call path: NewAPI relay /v1/responses -> Zen."""
    token = read_omp_relay_token()
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/v1/responses",
        method="POST",
        body={
            "model": MUSE_FREE_MODEL,
            "input": "say OK",
            "max_output_tokens": 16,
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=65,
    )
    if (
        status != 200
        or not isinstance(body, dict)
        or body.get("object") != "response"
        or body.get("error") is not None
    ):
        text = json.dumps(body) if isinstance(body, (dict, list)) else str(body)
        raise RuntimeError(f"relay responses probe failed: HTTP {status} {text[:200]!r}")


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        f"new-api-before-opencode-zen-free-{time.strftime('%Y%m%d-%H%M%S')}.db"
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


def merge_free_model_ratios(current: str) -> str:
    try:
        ratios = json.loads(current)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{MODEL_RATIO_OPTION} is invalid JSON") from error
    if not isinstance(ratios, dict):
        raise RuntimeError(f"{MODEL_RATIO_OPTION} must be a JSON object")
    for model in MODELS.split(","):
        ratios[model] = FREE_MODEL_RATIO
    return json.dumps(ratios, separators=(",", ":"), sort_keys=True)


def verify(
    db_path: Path, items: list[dict], channel_id: int,
    expected_status: int, strict: bool,
) -> None:
    """Readback-check the channel, its abilities, and the ModelRatio entries.

    strict=True (channel created by this run) also pins priority/weight and
    auto_ban. strict=False (pre-existing channel) checks identity fields only:
    Guardian's weight closed loop legitimately drifts posture on non-fixed
    routes, and a re-run must not fail on that drift.
    """
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
        rows = connection.execute(
            "SELECT model, enabled, priority, weight FROM abilities "
            "WHERE channel_id = ? ORDER BY model",
            (channel_id,),
        ).fetchall()
        ratio_row = connection.execute(
            "SELECT value FROM options WHERE key = ?", (MODEL_RATIO_OPTION,)
        ).fetchone()
    abilities = {model: (enabled, priority, weight) for model, enabled, priority, weight in rows}
    ability_enabled = 1 if expected_status == 1 else 0
    if strict:
        abilities_ok = abilities == {
            model: (ability_enabled, PRIORITY, WEIGHT) for model in MODELS.split(",")
        }
    else:
        abilities_ok = set(abilities) == set(MODELS.split(",")) and all(
            enabled == ability_enabled for enabled, _, _ in abilities.values()
        )
    ratio_ok = False
    if ratio_row is not None and isinstance(ratio_row[0], str):
        try:
            ratios = json.loads(ratio_row[0])
            ratio_ok = all(
                ratios.get(model) == FREE_MODEL_RATIO for model in MODELS.split(",")
            )
        except json.JSONDecodeError:
            ratio_ok = False
    if mismatch or not abilities_ok or not ratio_ok:
        raise RuntimeError(
            f"readback mismatch for ch{channel_id}: "
            f"channel={mismatch or 'ok'} abilities_ok={abilities_ok} "
            f"ratio_ok={ratio_ok}"
        )


def main() -> int:
    args = parse_args()
    key = args.key.strip()
    if not key:
        raise RuntimeError("key must not be empty")
    if args.apply and not args.accept_zen_free_data_policy:
        raise RuntimeError(
            "--apply requires --accept-zen-free-data-policy "
            "(Zen free tier / muse contributor data sharing)"
        )

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
    else:
        equivalent = [
            i
            for i in items
            if i.get("base_url") == BASE_URL and str(i.get("models") or "") == MODELS
        ]
        eq_ids = {int(i["id"]) for i in equivalent if isinstance(i.get("id"), int)}
        if len(eq_ids) > 1:
            raise RuntimeError(f"ambiguous equivalent channels: {sorted(eq_ids)}")
        if eq_ids:
            existing = next(i for i in equivalent if isinstance(i.get("id"), int))
    max_id = max(
        (int(i["id"]) for i in items if isinstance(i.get("id"), int)), default=0
    )
    planned_id = max_id + 1
    if existing is not None:
        print(f"plan: {CHANNEL_NAME} exists as ch{existing['id']}; verify only")
    else:
        print(
            f"plan: create {CHANNEL_NAME} as ch{planned_id} (key={mask(key)}) "
            f"disabled, probe ({TEST_MODEL}), set ModelRatio=0 for the free "
            f"models, enable at p{PRIORITY}/w{WEIGHT}, relay-probe "
            f"{MUSE_FREE_MODEL} via /v1/responses"
        )
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    created_new = False
    ratio_changed = False
    original_ratio = get_option_db(db_path, MODEL_RATIO_OPTION)
    try:
        if existing is not None:
            channel_id = int(existing["id"])
            # Never touch an existing channel's status: an intentional or
            # Guardian-driven disable must not be silently re-enabled by
            # re-running this script. Probe and verify only.
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
                # Double-lock: do not trust the create payload's status=2 to
                # be honored by the fork (same belt-and-suspenders as ch92).
                set_status(smoke, headers, channel_id, 2)
            created_new = True
            print(f"ch{channel_id} {CHANNEL_NAME} created disabled")

            probe_result = management_probe(smoke, headers, channel_id)
            print(f"ch{channel_id} management probe {probe_result} ({TEST_MODEL})")

            put_option(
                smoke, headers, MODEL_RATIO_OPTION,
                merge_free_model_ratios(original_ratio),
            )
            ratio_changed = True
            print(f"ModelRatio=0 set for the free models")

            set_status(smoke, headers, channel_id, 1)
            print(f"ch{channel_id} enabled at p{PRIORITY}/w{WEIGHT}")

            print(f"waiting {CACHE_SYNC_SECONDS}s for channel cache sync")
            time.sleep(CACHE_SYNC_SECONDS)
            relay_responses_probe(smoke)
            print(f"relay responses probe ok ({MUSE_FREE_MODEL})")

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
        # Roll back only what this run changed; a pre-existing channel keeps
        # its status untouched.
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


if __name__ == "__main__":
    raise SystemExit(main())
