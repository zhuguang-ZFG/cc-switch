#!/usr/bin/env python3
"""Onboard the jojatoken aggregator (https://jojatoken.com) into local NewAPI.

User decision 2026-10-09: aggregate all eight catalog models into the existing
pools (not a standalone parked tier) using FOUR keys supplied by the user.
Following the agentrouter four-key pool precedent (ch86+ch134/135/136), each
key gets its own single-key channel (jojatoken / jojatoken-2 / jojatoken-3 /
jojatoken-4) — this fork's multi-key channels have known pitfalls (t1qq:
creation does not persist is_multi_key; PUT regenerates channel_info), and
separate channels isolate a bad key instead of dragging the whole pool down.

First apply attempt 2026-10-09 16:32 rolled all three then-known channels back
to disabled: ch190 (key3) management probe hit a transient upstream
"do request failed" (direct re-probe 200×2 immediately after). --resume
re-probes existing disabled channels and enables them only on pass; new keys
are created through the standard path.

Direct upstream probes 2026-10-09 (OpenAI-compatible, no special headers /
no CF UA gate), all three keys:
- all 8 models chat/completions 200 with real content + usage
  (~300 prompt tokens of upstream injection overhead per call)
- gpt-6-astra tool_calls 200 (finish_reason=tool_calls, correct arguments)
- gpt-6-astra SSE 200 (chat.completion.chunk + finish stop + usage)
- gpt-6-astra /v1/responses 200 (both protocols available; channels are
  type=1 chat, matching the existing sol-family pool channels)

Pool placement (NewAPI priority desc, then weight within same priority):
- gpt-6-astra   -> existing pool: ch182 asvla p20/w5 primary, ch175 hubway
                   p-20; we join p-20/w1 (backup leg, asvla stays primary)
- gpt-5.6-luna / gpt-5.6-terra -> ch174 grok-heavy p-10 active; we join p-20/w1
- gpt-5.5 / gpt-5.6-sol / gpt-6.1-sol -> ch175 hubway p-20 active; we join
  p-20/w1 (1:1 aggregation with hubway)
- gpt-6-luna / gpt-6-sol -> NO active channel today (503 window); the three
  jojatoken channels become the only active legs for both

Pricing: gpt-6-sol / gpt-6.1-sol / gpt-6-astra have no ModelRatio (existing
open item — ch175/ch182 already run them at gateway default ratio). Read-only
per repo convention; do not change pricing here.

Workflow contract (same as the other add_* scripts): dup check, whole-DB
snapshot backup, create disabled, management probe while disabled, enable only
after probe passes, channel + abilities readback verify, then relay probes for
every pool model through 127.0.0.1:3002. Pool-level relay attribution cannot
prove the new channels for models with higher-priority active legs; channel
proof comes from the management probes + abilities readback.

Keys come from --keys-file (one key per line) and are never printed. Blank
lines and #-comments are ignored; duplicate keys are rejected. Re-running is
verify-only and never touches an existing channel's status/key.
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

CHANNEL_BASE_NAME = "jojatoken"
BASE_URL = "https://jojatoken.com"  # type=1 自动拼 /v1，base 不带 /v1
MODELS = [
    "gpt-5.5",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-6-sol",
    "gpt-6-luna",
    "gpt-6.1-sol",
    "gpt-6-astra",
]
MODELS_CSV = ",".join(MODELS)
TEST_MODEL = "gpt-5.6-sol"  # 家族主力池模型；实测管理探针 ~6s，远低于 65s 预算
PRIORITY = -20
WEIGHT = 1
CACHE_SYNC_SECONDS = 75
RELAY_TIMEOUT = 90
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
    parser.add_argument(
        "--keys-file",
        required=True,
        help="file with one API key per line (never printed; # comments allowed)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the backed-up live change; default is read-only",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="re-probe existing disabled channels and enable them on pass "
             "(recovery path for a rolled-back apply)",
    )
    return parser.parse_args()


def read_keys(path: Path) -> list[str]:
    keys: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        keys.append(entry)
    if not keys:
        raise RuntimeError(f"no keys found in {path}")
    if len(set(keys)) != len(keys):
        raise RuntimeError("duplicate keys in keys-file; refusing to pool them")
    return keys


def channel_name(index: int) -> str:
    return CHANNEL_BASE_NAME if index == 0 else f"{CHANNEL_BASE_NAME}-{index + 1}"


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


def channel_payload(name: str, key: str) -> dict:
    return {
        "name": name,
        "type": 1,  # OpenAI
        "key": key,
        "base_url": BASE_URL,
        "models": MODELS_CSV,
        "group": "default",
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
    """Prove the exact OMP call path: NewAPI relay /v1/chat/completions.

    Transient classes are retried with backoff (bounded, fail-closed):
    - 429/5xx/network: pool-level rate limits after probe bursts
    - 400 with anti-probe keywords: the upstream rejects short-input
      "distillation/heartbeat" probes (seen 2026-10-09 on gpt-5.6-terra),
      so the probe prompt is kept natural-length and retries back off.
    """
    import urllib.error

    token = read_omp_relay_token()
    attempts, backoff = 4, 15.0
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            time.sleep(backoff)
        try:
            status, body = smoke.http_json(
                f"{smoke.NEWAPI_BASE}/v1/chat/completions",
                method="POST",
                body={
                    "model": model,
                    "messages": [{
                        "role": "user",
                        "content": (
                            "This is a connectivity check between two systems. "
                            "Reply with exactly one word: OK. No other content."
                        ),
                    }],
                    "max_tokens": 800,  # 预防隐式推理吃光小 max_tokens 导致空 content
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=RELAY_TIMEOUT,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            # socket 超时/连接错误会以 TimeoutError/OSError 裸抛（http_json
            # 只捕 HTTPError），归入瞬态重试
            status, body, text = 0, {}, str(error)
        else:
            text = json.dumps(body) if isinstance(body, (dict, list)) else str(body)
            if status == 200 and isinstance(body, dict) and body.get("choices"):
                return
        anti_probe = status == 400 and any(
            k in text.lower() for k in ("distill", "heartbeat", "probing")
        )
        transient = status in (0, 429) or status >= 500 or anti_probe
        if not transient or attempt == attempts:
            raise RuntimeError(
                f"relay probe failed for {model}: HTTP {status} {text[:200]!r}"
            )
        print(f"relay probe {model}: HTTP {status} transient, retry in {backoff:.0f}s "
              f"({attempt}/{attempts})")


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        f"new-api-before-jojatoken-{time.strftime('%Y%m%d-%H%M%S')}.db"
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
    db_path: Path, items: list[dict], channel_id: int, name: str,
    expected_status: int, strict: bool,
) -> None:
    channel = next((i for i in items if i.get("id") == channel_id), None)
    if channel is None:
        raise RuntimeError(f"ch{channel_id} missing on readback")
    expected = {
        "name": name,
        "type": 1,
        "status": expected_status,
        "base_url": BASE_URL,
        "models": MODELS_CSV,
        "test_model": TEST_MODEL,
    }
    if strict:
        expected.update({"auto_ban": 1, "priority": PRIORITY, "weight": WEIGHT})
    mismatch = {
        field: (channel.get(field), value)
        for field, value in expected.items()
        if channel.get(field) != value
    }
    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        ability_enabled = 1 if expected_status == 1 else 0
        abilities_bad = []
        for model in MODELS:
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
    keys = read_keys(Path(args.keys_file))
    names = [channel_name(i) for i in range(len(keys))]

    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    token, user_id = smoke.admin_auth()
    headers = {
        "Authorization": f"Bearer {token}",
        "New-Api-User": str(user_id),
    }
    items = list_channels(smoke, headers)

    # dup check by name across all planned channels
    planned_names = set(names)
    existing: dict[str, dict] = {}
    for i in items:
        if i.get("name") in planned_names and isinstance(i.get("id"), int):
            existing[str(i["name"])] = i
    if len(existing) == len(names):
        print("plan: all channels exist; verify only")
    else:
        todo = sorted(planned_names - set(existing))
        print(
            f"plan: create {len(todo)} channel(s) {todo} "
            f"(keys={','.join(mask(k) for k in keys)}), disabled -> probe "
            f"({TEST_MODEL}) -> enable at p{PRIORITY}/w{WEIGHT}, then "
            f"relay-probe {len(MODELS)} models via 3002"
        )
    # model-name collision check: pool members are expected for the aggregated
    # pools; note them
    for i in items:
        if i.get("name") in planned_names:
            continue
        models = {m.strip() for m in str(i.get("models") or "").split(",")}
        shared = sorted(models & set(MODELS))
        if shared:
            print(
                f"note: ch{i.get('id')} {i.get('name')} (status={i.get('status')}) "
                f"also declares {','.join(shared)} — pool will aggregate"
            )
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    created_new: list[tuple[int, str]] = []
    resumed: list[tuple[int, str]] = []
    touched: list[tuple[int, str]] = []
    try:
        for index, (name, key) in enumerate(zip(names, keys)):
            current = existing.get(name)
            if current is not None:
                channel_id = int(current["id"])
                if args.resume and current.get("status") == 2:
                    # fail-closed: management_probe raises on hard failure,
                    # so a broken channel is never re-enabled
                    probe_result = management_probe(smoke, headers, channel_id)
                    print(f"ch{channel_id} {name} resume probe {probe_result} ({TEST_MODEL})")
                    set_status(smoke, headers, channel_id, 1)
                    resumed.append((channel_id, name))
                    print(f"ch{channel_id} {name} enabled at p{PRIORITY}/w{WEIGHT}")
                else:
                    probe_result = management_probe(smoke, headers, channel_id)
                    print(
                        f"ch{channel_id} {name} exists "
                        f"(status={current.get('status')}); probe {probe_result}, "
                        f"status untouched"
                    )
                    touched.append((channel_id, name))
                continue

            items = list_channels(smoke, headers)
            planned_id = max(
                (int(i["id"]) for i in items if isinstance(i.get("id"), int)),
                default=0,
            ) + 1
            status, body = smoke.http_json(
                f"{smoke.NEWAPI_BASE}/api/channel/",
                method="POST",
                body={"mode": "single", "channel": channel_payload(name, key)},
                headers=headers,
            )
            if status != 200 or not isinstance(body, dict) or not body.get("success"):
                message = body.get("message") if isinstance(body, dict) else None
                raise RuntimeError(
                    f"create {name} failed: HTTP {status} message={message!r}"
                )
            items = list_channels(smoke, headers)
            created = next((i for i in items if i.get("name") == name), None)
            if created is None or not isinstance(created.get("id"), int):
                raise RuntimeError(f"created {name} missing on readback")
            channel_id = int(created["id"])
            if channel_id != planned_id:
                set_status(smoke, headers, channel_id, 2)
                raise RuntimeError(
                    f"created unexpected channel id {channel_id} for {name}; "
                    f"expected {planned_id} (left disabled; manual "
                    f"enable or delete + re-run required)"
                )
            if created.get("status") != 2:
                set_status(smoke, headers, channel_id, 2)
            created_new.append((channel_id, name))
            print(f"ch{channel_id} {name} created disabled")

            probe_result = management_probe(smoke, headers, channel_id)
            print(f"ch{channel_id} {name} management probe {probe_result} ({TEST_MODEL})")

            set_status(smoke, headers, channel_id, 1)
            print(f"ch{channel_id} {name} enabled at p{PRIORITY}/w{WEIGHT}")

        if created_new or resumed:
            print(f"waiting {CACHE_SYNC_SECONDS}s for channel cache sync")
            time.sleep(CACHE_SYNC_SECONDS)
            for model in MODELS:
                relay_probe(smoke, model)
                time.sleep(4)  # 节流：避免连发短探针触发上游反探测风控
                print(f"relay probe ok ({model} via 3002)")

        items = list_channels(smoke, headers)
        for channel_id, name in created_new + resumed:
            verify(db_path, items, channel_id, name, expected_status=1, strict=True)
        for channel_id, name in touched:
            current = next((i for i in items if i.get("id") == channel_id), None)
            status_value = current.get("status") if current else None
            if not isinstance(status_value, int):
                raise RuntimeError(f"ch{channel_id} status unavailable in API projection")
            verify(db_path, items, channel_id, name,
                   expected_status=status_value, strict=False)
        ids = ",".join(f"ch{cid}" for cid, _ in created_new + resumed + touched)
        print(
            f"OK: {ids} {CHANNEL_BASE_NAME} pool live at p{PRIORITY}/w{WEIGHT} "
            f"({len(created_new)} created, {len(resumed)} resumed, "
            f"{len(touched)} pre-existing); backup={backup.name}"
        )
        return 0
    except Exception:
        for channel_id, name in created_new + resumed:
            try:
                set_status(smoke, headers, channel_id, 2)
                print(f"rollback: ch{channel_id} {name} disabled")
            except Exception as error:
                print(f"rollback warning: could not disable ch{channel_id}: {error}")
        print(f"rollback attempted; full snapshot={backup.name}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
