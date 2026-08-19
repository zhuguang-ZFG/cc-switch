#!/usr/bin/env python3
"""Repair only the fixed NewAPI posture for the OpenCode Go Muse channel.

The default mode is read-only. Apply mode requires two forced streaming
Responses probes while ch48 is enabled, creates an integrity-checked online
SQLite backup, updates only channel/ability priority and weight, and rolls back
on any failed readback or post-change probe. A disabled ch48 remains disabled.
"""

from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import time
from contextlib import closing
from pathlib import Path


SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
CHANNEL_ID = 48
CHANNEL_NAME = "opencode-go-muse"
CHANNEL_BASE_URL = "https://opencode.ai/zen/go"
MODEL = "muse-spark-1.2-contributor"
PRIORITY = 51
WEIGHT = 12
# Keep enough room for low-effort reasoning before the short semantic answer.
SEMANTIC_MAX_OUTPUT_TOKENS = 512
CACHE_SYNC_SECONDS = 75


def load_smoke():
    spec = importlib.util.spec_from_file_location(
        "newapi_local_smoke_for_muse_posture", SMOKE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--cache-wait", type=int, default=CACHE_SYNC_SECONDS)
    args = parser.parse_args()
    if args.cache_wait < 0:
        parser.error("--cache-wait must not be negative")
    return args


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / (
        "new-api-before-muse-posture-" + time.strftime("%Y%m%d-%H%M%S") + ".db"
    )
    if destination.exists():
        raise RuntimeError(f"backup already exists: {destination}")
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as source, closing(sqlite3.connect(destination, timeout=30)) as target:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RuntimeError("backup integrity check failed")
    return destination


def read_state(connection: sqlite3.Connection) -> dict:
    channel = connection.execute(
        "SELECT name, status, type, priority, weight, typeof(channel_info), "
        "base_url, models FROM channels WHERE id = ?",
        (CHANNEL_ID,),
    ).fetchone()
    if channel is None:
        raise RuntimeError(f"ch{CHANNEL_ID} is missing")
    if channel[0] != CHANNEL_NAME:
        raise RuntimeError(
            f"ch{CHANNEL_ID} expected {CHANNEL_NAME!r}, got {channel[0]!r}"
        )
    if channel[2] != 1 or channel[6] != CHANNEL_BASE_URL:
        raise RuntimeError(f"ch{CHANNEL_ID} provider identity drift")
    if channel[5] != "blob":
        raise RuntimeError(f"ch{CHANNEL_ID} channel_info must remain a BLOB")
    if channel[7] != MODEL:
        raise RuntimeError(f"ch{CHANNEL_ID} model projection drift")

    abilities = [
        tuple(row)
        for row in connection.execute(
            "SELECT `group`, model, enabled, priority, weight FROM abilities "
            "WHERE channel_id = ? ORDER BY `group`, model",
            (CHANNEL_ID,),
        )
    ]
    if len(abilities) != 1 or abilities[0][1] != MODEL:
        raise RuntimeError(f"ch{CHANNEL_ID} ability projection drift")
    return {"channel": tuple(channel), "abilities": abilities}


def write_target(connection: sqlite3.Connection) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        channel_result = connection.execute(
            "UPDATE channels SET priority = ?, weight = ? WHERE id = ?",
            (PRIORITY, WEIGHT, CHANNEL_ID),
        )
        ability_result = connection.execute(
            "UPDATE abilities SET priority = ?, weight = ? WHERE channel_id = ?",
            (PRIORITY, WEIGHT, CHANNEL_ID),
        )
        if channel_result.rowcount != 1 or ability_result.rowcount != 1:
            raise RuntimeError("Muse posture update touched unexpected rows")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def restore_state(connection: sqlite3.Connection, original: dict) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        channel = original["channel"]
        connection.execute(
            "UPDATE channels SET priority = ?, weight = ? WHERE id = ?",
            (channel[3], channel[4], CHANNEL_ID),
        )
        for group, model, _enabled, priority, weight in original["abilities"]:
            connection.execute(
                "UPDATE abilities SET priority = ?, weight = ? "
                "WHERE channel_id = ? AND `group` = ? AND model = ?",
                (priority, weight, CHANNEL_ID, group, model),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def verify_target(connection: sqlite3.Connection, original: dict) -> dict:
    current = read_state(connection)
    channel = current["channel"]
    if channel[1] != original["channel"][1]:
        raise RuntimeError("Muse channel status changed unexpectedly")
    if channel[3:5] != (PRIORITY, WEIGHT):
        raise RuntimeError(f"Muse channel posture is {channel[3]}/{channel[4]}")
    original_enabled = [row[2] for row in original["abilities"]]
    if [row[2] for row in current["abilities"]] != original_enabled:
        raise RuntimeError("Muse ability enabled state changed unexpectedly")
    if any(row[3:5] != (PRIORITY, WEIGHT) for row in current["abilities"]):
        raise RuntimeError("Muse ability posture mismatch")
    return current


def management_probe(smoke, headers: dict[str, str]) -> None:
    path = (
        f"/api/channel/test/{CHANNEL_ID}?model={MODEL}"
        "&endpoint_type=openai-response&stream=true"
    )
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}{path}", headers=headers, timeout=65
    )
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        message = body.get("message") if isinstance(body, dict) else None
        raise RuntimeError(
            f"ch{CHANNEL_ID} management probe failed: "
            f"HTTP {status} message={message!r}"
        )


def response_text(payload: dict) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str):
        return direct
    fragments: list[str] = []
    output = payload.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                continue
            text = part.get("text")
            if isinstance(text, str):
                fragments.append(text)
    return "".join(fragments)


def read_client_key(smoke) -> str:
    payload = smoke.read_json(Path(smoke.DEPLOY_DIR) / "client-token.json")
    key = payload.get("api_key") or payload.get("key")
    if not isinstance(key, str) or not key.strip():
        raise RuntimeError("local NewAPI client token is missing")
    return key.strip()


def latest_log_id(db_path: Path) -> int:
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        return int(
            connection.execute("SELECT COALESCE(MAX(id), 0) FROM logs").fetchone()[0]
        )


def require_log_attribution(db_path: Path, after_id: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with closing(
            sqlite3.connect(
                f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
            )
        ) as connection:
            row = connection.execute(
                "SELECT channel_id, model_name FROM logs "
                "WHERE id > ? AND model_name = ? ORDER BY id DESC LIMIT 1",
                (after_id, MODEL),
            ).fetchone()
        if row is not None:
            if int(row[0]) != CHANNEL_ID:
                raise RuntimeError(
                    f"Muse aggregate log attributed to channel {row[0]}, "
                    f"expected {CHANNEL_ID}"
                )
            return
        time.sleep(0.25)
    raise RuntimeError("Muse aggregate request is missing from NewAPI logs")


def aggregate_probe(smoke, db_path: Path, key: str) -> None:
    last_id = latest_log_id(db_path)
    status, payload = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/v1/responses",
        method="POST",
        timeout=60,
        headers={"Authorization": f"Bearer {key}"},
        body={
            "model": MODEL,
            "max_output_tokens": SEMANTIC_MAX_OUTPUT_TOKENS,
            "reasoning": {"effort": "low"},
            "input": "Reply only: OK.",
        },
    )
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"Muse aggregate Responses probe failed: HTTP {status}")
    if (
        payload.get("status") == "incomplete"
        or payload.get("incomplete_details") is not None
    ):
        raise RuntimeError("Muse aggregate Responses probe returned incomplete output")
    text = response_text(payload).strip()
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(
        usage.get("output_tokens") or usage.get("completion_tokens") or 0
    )
    if text != "OK" or input_tokens <= 0 or output_tokens <= 0:
        raise RuntimeError(
            "Muse aggregate semantic/usage contract failed: "
            f"text_ok={text == 'OK'} usage_ok={input_tokens > 0 and output_tokens > 0}"
        )
    require_log_attribution(db_path, last_id)


def main() -> int:
    args = parse_args()
    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        original = read_state(connection)

    channel = original["channel"]
    ability = original["abilities"][0]
    print(
        f"ch{CHANNEL_ID} {CHANNEL_NAME}: channel {channel[3]}/{channel[4]} -> "
        f"{PRIORITY}/{WEIGHT}; ability {ability[3]}/{ability[4]} -> "
        f"{PRIORITY}/{WEIGHT}; status preserved={channel[1]}"
    )
    already_target = (
        channel[3:5] == (PRIORITY, WEIGHT)
        and ability[3:5] == (PRIORITY, WEIGHT)
    )
    if not args.apply:
        print("dry-run: no changes made")
        return 0
    if already_target:
        print("already converged: no changes made")
        return 0

    token, user_id = smoke.admin_auth()
    headers = {
        "Authorization": f"Bearer {token}",
        "New-Api-User": str(user_id),
    }
    client_key = ""
    if int(channel[1]) == 1:
        for _ in range(2):
            management_probe(smoke, headers)
        client_key = read_client_key(smoke)
        aggregate_probe(smoke, db_path, client_key)
        print(
            "preflight ok: ch48 forced Responses probes 2/2; "
            "aggregate semantic/usage/log attribution ok"
        )
    else:
        print("preflight bounded: ch48 is disabled and will remain isolated")

    backup = online_backup(db_path)
    print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity=ok)")
    changed = False
    try:
        with closing(sqlite3.connect(db_path, timeout=30)) as connection:
            write_target(connection)
            changed = True
            verify_target(connection, original)
            if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise RuntimeError("live database integrity check failed")

        if args.cache_wait:
            print(f"posture written; waiting {args.cache_wait}s for channel cache")
            time.sleep(args.cache_wait)
        if int(channel[1]) == 1:
            management_probe(smoke, headers)
            aggregate_probe(smoke, db_path, client_key)
        with closing(
            sqlite3.connect(
                f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
            )
        ) as connection:
            verify_target(connection, original)
        print(
            f"OK: ch{CHANNEL_ID} Muse posture {PRIORITY}/{WEIGHT}; "
            f"status preserved={channel[1]}; backup={backup.name}"
        )
        return 0
    except Exception:
        if changed:
            try:
                with closing(sqlite3.connect(db_path, timeout=30)) as connection:
                    restore_state(connection, original)
                    if read_state(connection) != original:
                        raise RuntimeError("rollback readback differs from original")
                if args.cache_wait:
                    time.sleep(args.cache_wait)
            except Exception as rollback_error:
                print(f"rollback warning: {rollback_error}; snapshot={backup.name}")
        print(f"apply failed; snapshot={backup.name}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
