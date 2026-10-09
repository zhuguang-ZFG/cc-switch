#!/usr/bin/env python3
"""Onboard the longai aggregator (https://llm.longai.vip) into local NewAPI.

User decision 2026-10-09: aggregate the shared key (valid until 2026-10-20)
into the existing pools. The upstream is itself a NewAPI instance.

Direct upstream probes 2026-10-09 (OpenAI-compatible, no special headers):
- glm-5.2 / glm-5.3-flash / deepseek-v4-pro / kimi-k2.6 / kimi-k2.7-code /
  qwen3.8-max: chat 200 with real content + usage (~180 prompt tokens of
  upstream injection)
- glm-5.3: chat 200 but SLOW (90s timeout at first attempt, 59.8s on the
  240s re-probe). Included as a p19 fallback leg only; relay probe gets a
  240s budget; test_model is glm-5.3-flash so channel-level tests never
  hit the slow model. Guardian slow-channel detection (3x > 60s) will
  degrade it if it stays slow — that is the system working as designed.
- qwen3.8-flash: 503 "all selected channels are cooling down" from the
  upstream's own distributor, twice — NOT included (hashneuron/seeseed
  precedent: only register directly-verified-alive models). Its ModelRatio
  is also missing locally. Re-add after the upstream recovers + pricing.

Pool placement (p19/w1 — strictly below every existing primary for the
aggregated models, above nothing; gap models get their first active leg):
- glm-5.3          -> intern-discovery p40 x5 active; we join p19 fallback
- glm-5.2          -> ch173 muyuan-gongyi p20/w1 active; we join p19 backup
- qwen3.8-flash    -> ch186 tierflow p-30 active (excluded here anyway)
- glm-5.3-flash / deepseek-v4-pro / kimi-k2.6 / kimi-k2.7-code /
  qwen3.8-max -> NO active channel today; longai becomes the sole active leg

Pricing: all included models already have ModelRatio (glm-5.2=2, glm-5.3=0.7,
glm-5.3-flash=0.075, deepseek-v4-pro/kimi-k2.6/kimi-k2.7-code/qwen3.8-max=0.5).
Read-only; nothing changed.

Workflow contract (same as the other add_* scripts): dup check, whole-DB
snapshot backup, create disabled, management probe while disabled, enable only
after probe passes, channel + abilities readback verify, then relay probes for
every pool model through 127.0.0.1:3002 (pacing + anti-probe-aware retries;
see relay_probe). Pool-level relay attribution cannot prove the new channel
for models with higher-priority active legs; channel proof comes from the
management probe + abilities readback.

Key comes from --keys-file (one line) and is never printed. Re-running is
verify-only (--resume additionally re-probes and re-enables disabled
channels after a rolled-back apply).
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

CHANNEL_NAME = "longai"
BASE_URL = "https://llm.longai.vip"  # type=1 自动拼 /v1，base 不带 /v1
MODELS = [
    "glm-5.2",
    "glm-5.3",
    "glm-5.3-flash",
    "deepseek-v4-pro",
    "kimi-k2.6",
    "kimi-k2.7-code",
    "qwen3.8-max",
]
MODELS_CSV = ",".join(MODELS)
TEST_MODEL = "glm-5.3-flash"  # flash 类最快；刻意避开慢速 glm-5.3
PRIORITY = 19
WEIGHT = 1
CACHE_SYNC_SECONDS = 75
RELAY_TIMEOUT_DEFAULT = 240  # code/推理模型 TTFT 方差大，90s 会裸超时
RELAY_TIMEOUT = {"glm-5.3": 240}  # glm-5.3 实测首响 ~60s，给足预算
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
    429/5xx/network, and 400 responses carrying anti-probe keywords (the
    jojatoken upstream rejects short-input probing; the hardened prompt
    shape is reused here for uniformity).
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
                timeout=RELAY_TIMEOUT.get(model, RELAY_TIMEOUT_DEFAULT),
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
        f"new-api-before-longai-{time.strftime('%Y%m%d-%H%M%S')}.db"
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
    if len(keys) != 1:
        raise RuntimeError(f"{CHANNEL_NAME} is a single-key channel; got {len(keys)} keys")
    key = keys[0]

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
    for i in items:
        if i.get("name") == CHANNEL_NAME:
            continue
        models = {m.strip() for m in str(i.get("models") or "").split(",")}
        shared = sorted(models & set(MODELS))
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
            f"relay-probe {len(MODELS)} models via 3002"
        )
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    resumed = False
    channel_id: int | None = None
    try:
        if existing is not None:
            channel_id = int(existing["id"])
            if args.resume and existing.get("status") == 2:
                probe_result = management_probe(smoke, headers, channel_id)
                print(f"ch{channel_id} resume probe {probe_result} ({TEST_MODEL})")
                set_status(smoke, headers, channel_id, 1)
                resumed = True
                print(f"ch{channel_id} enabled at p{PRIORITY}/w{WEIGHT}")
            else:
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
            resumed = True
            print(f"ch{channel_id} {CHANNEL_NAME} created disabled")

            probe_result = management_probe(smoke, headers, channel_id)
            print(f"ch{channel_id} management probe {probe_result} ({TEST_MODEL})")

            set_status(smoke, headers, channel_id, 1)
            print(f"ch{channel_id} enabled at p{PRIORITY}/w{WEIGHT}")

        if resumed:
            print(f"waiting {CACHE_SYNC_SECONDS}s for channel cache sync")
            time.sleep(CACHE_SYNC_SECONDS)
            for model in MODELS:
                relay_probe(smoke, model)
                time.sleep(4)  # 节流：避免连发短探针触发上游反探测风控
                print(f"relay probe ok ({model} via 3002)")

        items = list_channels(smoke, headers)
        verify(db_path, items, channel_id,
               expected_status=1 if resumed else int(existing["status"]),  # type: ignore[index]
               strict=resumed)
        state = "live" if resumed else "present"
        print(
            f"OK: ch{channel_id} {CHANNEL_NAME} {state} at p{PRIORITY}/w{WEIGHT}; "
            f"backup={backup.name}"
        )
        return 0
    except Exception:
        if resumed and channel_id is not None:
            try:
                set_status(smoke, headers, channel_id, 2)
                print(f"rollback: ch{channel_id} disabled")
            except Exception as error:
                print(f"rollback warning: could not disable ch{channel_id}: {error}")
        print(f"rollback attempted; full snapshot={backup.name}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
