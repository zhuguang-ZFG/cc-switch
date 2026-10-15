#!/usr/bin/env python3
"""Recover ch178 zen-free-bridge rows after the bridge CLI recovered (2026-10-15).

History: 2026-10-11..13 the bridge's opencode CLI hung (150-180s timeouts,
leaked grandchild processes) and the unanswerable free ids were removed from
ch178 (big-pickle, mimo-v2.6-flash-free, ling-3.1-flash-free, space-bunny-free,
muse-spark-1.3-contributor-free, ling-3.0-flash-fin-free). The bridge has
recovered: on 2026-10-15 it was still serving nemotron-3.5-lightning-free /
longcat-2.5-preview-free / muse-spark-1.2-contributor-free / step-5-preview-free
through NewAPI (ch178 last log 17:03).

This syncs the channel to live evidence instead of a hardcoded catalog:

- catalog = ``opencode models`` (the genuine CLI's own list) filtered to
  ``opencode/*`` — the same CLI the bridge spawns;
- live    = a model either answered a real tiny completion through the bridge
  in this run (HTTP 200; 429 = free-quota exhausted, treated as unknown and
  never auto-added; 502/timeout = dead) or served real traffic through ch178
  within the last 24h (probe savings);
- target  = (current channel models | catalog) that are live.

--apply: whole-DB snapshot -> PUT /api/channel/ with the target models string
-> ModelRatio=0 for added ids, drop entries for removed ids -> readback verify
(models string, abilities enabled rows, ratios) -> admin test of one added id
through the NewAPI gateway path. Any failure restores the original channel
payload and ModelRatio (full snapshot kept for the DB).

Probe results are cached in ~/.new-api-local/zen-bridge-probe.json (30 min TTL)
so a dry run can be reviewed before --apply without paying the free quota
twice. Dry-run (default) never writes.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path

SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
CHANNEL_NAME = "zen-free-bridge"
BRIDGE_URL = "http://127.0.0.1:8412/v1/chat/completions"
OPCODE_BIN = Path("C:/Users/zhugu/AppData/Roaming/npm/opencode.cmd")
PROBE_CACHE = Path.home() / ".new-api-local" / "zen-bridge-probe.json"
PROBE_TTL_SECONDS = 1800
PROBE_TIMEOUT = 170.0
SERVED_LOOKBACK_SECONDS = 24 * 3600
MODEL_RATIO_OPTION = "ModelRatio"
FREE_MODEL_RATIO = 0
ABILITIES_WAIT_SECONDS = 90


def load_smoke():
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", SMOKE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load smoke helper: {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="apply the probed target; default is read-only")
    parser.add_argument("--refresh", action="store_true",
                        help="ignore the probe cache and re-probe every candidate")
    parser.add_argument("--allow-drop", action="store_true",
                        help="also remove current models that fail probing and have no"
                             " real completion within 24h (default: additive only)")
    return parser.parse_args()


def load_catalog() -> list[str]:
    """opencode/* ids from the genuine CLI catalog (no quota cost)."""
    out = subprocess.run(
        [str(OPCODE_BIN), "models"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
    )
    if out.returncode != 0:
        raise RuntimeError(f"`opencode models` failed: rc={out.returncode} {out.stderr[:200]!r}")
    ids = []
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("opencode/"):
            ids.append(line.split("/", 1)[1])
    if not ids:
        raise RuntimeError("`opencode models` produced no opencode/* ids")
    return ids


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


def recently_served(db_path: Path, channel_id: int, model: str) -> bool:
    """True when the channel really completed this model within the lookback.

    Requires a real completion: free models log quota=0, so quota cannot be the
    marker (2026-10-15: single-shot bridge 502s are transient -- nemotron-3.5 and
    longcat both 502'd then answered 200 twenty minutes later).
    """
    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM logs WHERE channel_id=? AND model_name=? AND type=2"
            " AND completion_tokens>0 AND created_at>=?",
            (channel_id, model, int(time.time()) - SERVED_LOOKBACK_SECONDS),
        ).fetchone()
    return bool(row and row[0] > 0)


def probe_bridge(smoke, model: str) -> dict:
    """One real completion through the bridge.

    Retries because both failure shapes flake: upstream 502s (nemotron-3.5 and
    longcat 502'd then answered 200 twenty minutes later on 2026-10-15) and 503
    "zen bridge busy" (single-flight contention with real gateway traffic, not a
    model fault). Only HTTP 200 with content counts as live.
    """
    last = None
    for attempt in range(3):
        last = _probe_once(smoke, model)
        if last["live"]:
            break
        if attempt < 2:
            time.sleep(20)
    last["attempts"] = attempt + 1
    return last


def _probe_once(smoke, model: str) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word: OK"}],
        "max_tokens": 24,
        "stream": False,
    }
    started = time.time()
    try:
        status, payload = smoke.http_json(
            BRIDGE_URL, method="POST", body=body, timeout=PROBE_TIMEOUT
        )
    except Exception as error:  # URLError/timeout/connection reset
        return {"model": model, "status": None, "seconds": round(time.time() - started, 1),
                "live": False, "quota": False,
                "detail": f"{type(error).__name__}: {error}"[:160]}
    seconds = round(time.time() - started, 1)
    message = {}
    if isinstance(payload, dict):
        choices = payload.get("choices") or []
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
    text = str(message.get("content") or message.get("reasoning_content") or "").strip()
    live = status == 200 and bool(text)
    return {
        "model": model,
        "status": status,
        "seconds": seconds,
        "live": live,
        "quota": status == 429,
        "detail": text[:60] if live else json.dumps(payload)[:160] if isinstance(payload, dict) else "",
    }


def load_probe_cache() -> dict:
    try:
        data = json.loads(PROBE_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or time.time() - float(data.get("ts") or 0) > PROBE_TTL_SECONDS:
        return {}
    return data.get("results") or {}


def save_probe_cache(results: dict) -> None:
    PROBE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    PROBE_CACHE.write_text(
        json.dumps({"ts": time.time(), "results": results}, indent=1), encoding="utf-8"
    )


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        f"new-api-before-zen-free-bridge-recover-{time.strftime('%Y%m%d-%H%M%S')}.db"
    )
    if destination.exists():
        raise RuntimeError(f"backup already exists: {destination}")
    source = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
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


def put_channel(smoke, headers: dict[str, str], payload: dict) -> None:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/", method="PUT", body=payload, headers=headers
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(f"channel PUT failed: HTTP {status} message={message!r}")


def get_option_db(db_path: Path, key: str) -> str:
    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        row = connection.execute("SELECT value FROM options WHERE key=?", (key,)).fetchone()
    if row is None or not isinstance(row[0], str):
        raise RuntimeError(f"option {key!r} is missing")
    return row[0]


def put_option(smoke, headers: dict[str, str], key: str, value: str) -> None:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/option/", method="PUT",
        body={"key": key, "value": value}, headers=headers,
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"option {key!r} update failed: HTTP {status}")


def merge_ratio(current: str, removed: tuple[str, ...], added: tuple[str, ...]) -> str:
    try:
        ratios = json.loads(current)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{MODEL_RATIO_OPTION} is invalid JSON") from error
    if not isinstance(ratios, dict):
        raise RuntimeError(f"{MODEL_RATIO_OPTION} must be a JSON object")
    for model in removed:
        ratios.pop(model, None)
    for model in added:
        ratios[model] = FREE_MODEL_RATIO
    return json.dumps(ratios, separators=(",", ":"), sort_keys=True)


def compute_target(candidates: list[str], current: list[str],
                   results: dict[str, dict], allow_drop: bool
                   ) -> tuple[list[str], tuple[str, ...], tuple[str, ...]]:
    """Target model list under the additive (default) or drop policy.

    Additive keeps every current model even when probing failed: single-shot
    502s and 503-busy were both proven transient on 2026-10-15 (nemotron-3.5 /
    longcat 502'd then answered 200 twenty minutes later; 503 is single-flight
    contention with real gateway traffic). Dropping needs clean-window
    evidence, so it stays behind --allow-drop.
    """
    if allow_drop:
        target = [m for m in candidates if results[m]["live"]]
    else:
        target = [m for m in candidates if m in current or results[m]["live"]]
    added = tuple(m for m in target if m not in current)
    removed = tuple(m for m in current if m not in target)
    return target, added, removed


def wait_abilities(db_path: Path, channel_id: int, models: list[str]) -> None:
    deadline = time.time() + ABILITIES_WAIT_SECONDS
    pending = list(models)
    while pending and time.time() < deadline:
        with closing(sqlite3.connect(
            f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
        )) as connection:
            pending = [
                model for model in models
                if connection.execute(
                    "SELECT enabled FROM abilities WHERE channel_id=? AND model=?",
                    (channel_id, model),
                ).fetchone() != (1,)
            ]
        if pending:
            time.sleep(5)
    if pending:
        raise RuntimeError(f"abilities rows not enabled after {ABILITIES_WAIT_SECONDS}s: {pending}")


def verify(db_path: Path, channel: dict, target: str,
           removed: tuple[str, ...], added: tuple[str, ...]) -> None:
    if channel.get("models") != target:
        raise RuntimeError(f"readback models mismatch: {channel.get('models')!r} != {target!r}")
    wait_abilities(db_path, int(channel["id"]), [m for m in target.split(",") if m])
    with closing(sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
    )) as connection:
        row = connection.execute(
            "SELECT value FROM options WHERE key=?", (MODEL_RATIO_OPTION,)
        ).fetchone()
    if row is None:
        raise RuntimeError(f"{MODEL_RATIO_OPTION} missing on readback")
    ratios = json.loads(row[0])
    for model in added:
        if ratios.get(model) != FREE_MODEL_RATIO:
            raise RuntimeError(f"ModelRatio for {model} != {FREE_MODEL_RATIO}")
    for model in removed:
        if model in ratios:
            raise RuntimeError(f"ModelRatio entry for {model} still present")


def main() -> int:
    args = parse_args()
    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    channel = fetch_channel(smoke, headers)
    channel_id = int(channel["id"])
    current = [m for m in str(channel.get("models") or "").split(",") if m]
    catalog = load_catalog()

    # current order first (stable), then catalog-only ids
    candidates = list(dict.fromkeys(current + [m for m in catalog if m not in current]))
    cache = {} if args.refresh else load_probe_cache()
    results: dict[str, dict] = {}
    for model in candidates:
        if recently_served(db_path, channel_id, model):
            results[model] = {"model": model, "status": 200, "seconds": None,
                              "live": True, "quota": False, "detail": "served within 24h"}
            print(f"served {model}: live (real traffic within 24h, no probe)")
            continue
        if model in cache:
            results[model] = cache[model]
            print(f"cached {model}: live={cache[model]['live']} status={cache[model]['status']}")
            continue
        result = probe_bridge(smoke, model)
        results[model] = result
        state = "LIVE" if result["live"] else ("QUOTA" if result["quota"] else "dead")
        print(f"probe {model}: {state} status={result['status']} {result['seconds']}s {result['detail']}")
    save_probe_cache(results)

    target_list, added, removed = compute_target(candidates, current, results, args.allow_drop)
    target = ",".join(target_list)
    unknown = [m for m in candidates if not results[m]["live"] and results[m].get("quota")]
    busy = [m for m in candidates if not results[m]["live"] and results[m].get("status") == 503]
    dead = [m for m in candidates
            if not results[m]["live"] and m not in unknown and m not in busy]

    print(f"\nplan: ch{channel_id} {CHANNEL_NAME} (additive{' , with drop' if args.allow_drop else ''})")
    print(f"  keep    : {[m for m in current if m in target_list]}")
    print(f"  re-add  : {list(added)}")
    print(f"  drop    : {list(removed)}")
    print(f"  dead    : {dead}")
    print(f"  busy    : {busy}")
    print(f"  quota?  : {unknown}")
    print(f"  catalog ids not live: {[m for m in catalog if not results[m]['live']]}")
    if not args.apply:
        print("dry-run: no changes made")
        return 0
    if not added and not removed:
        print(f"OK: ch{channel_id} models already match live evidence; verify only")
        return 0

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")

    original_payload = {k: v for k, v in channel.items() if k != "status"}
    original_ratio = get_option_db(db_path, MODEL_RATIO_OPTION)
    ratio_changed = False
    try:
        updated_payload = dict(original_payload)
        updated_payload["models"] = target
        put_channel(smoke, headers, updated_payload)
        print(f"ch{channel_id} models -> {target}")

        target_ratio = merge_ratio(original_ratio, removed, added)
        if target_ratio != original_ratio:
            put_option(smoke, headers, MODEL_RATIO_OPTION, target_ratio)
            ratio_changed = True
            print(f"ModelRatio synced (added={list(added)}, removed={list(removed)})")

        readback = fetch_channel(smoke, headers)
        verify(db_path, readback, target, removed, added)
        print(f"readback OK: models/abilities/ratios match")

        if added:
            probe_model = added[0]
            status, body = smoke.http_json(
                f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={probe_model}",
                headers=headers, timeout=PROBE_TIMEOUT + 30,
            )
            ok = status == 200 and isinstance(body, dict) and body.get("success") is not False
            print(f"admin test {probe_model}: HTTP {status} success={body.get('success') if isinstance(body, dict) else None}")
            if not ok:
                raise RuntimeError(f"admin test failed: HTTP {status} {str(body)[:200]}")

        print(f"OK: ch{channel_id} {CHANNEL_NAME} synced to {len(target_list)} live models; "
              f"backup={backup.name}")
        return 0
    except Exception:
        put_channel(smoke, headers, original_payload)
        print("rollback: channel payload restored")
        if ratio_changed:
            put_option(smoke, headers, MODEL_RATIO_OPTION, original_ratio)
            print("rollback: ModelRatio restored")
        print(f"rollback attempted; full snapshot={backup.name}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
