#!/usr/bin/env python3
"""Repair ch91 so real Codex Responses requests survive the NewAPI hop.

The jianzhile relay accepts a complete Codex /v1/responses envelope but
rejects a plain Chat Completions request with 403. This helper adds an
isolated model alias, enables channel-local Chat-to-Responses conversion,
pins a safe fallback Codex header envelope, and preserves the multi-key BLOB.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import time
import uuid
from pathlib import Path


CHANNEL_ID = 91
CHANNEL_NAME = "jianzhile-gpt-5.6-sol"
BASE_URL = "https://jianzhile.vip"
CODEX_MODEL = "jianzhile-codex-gpt-5.6-sol"
UPSTREAM_MODEL = "gpt-5.6-sol"
PRIORITY = 50
WEIGHT = 5
DEFAULT_DB = Path.home() / ".new-api-local" / "new-api.db"
CHAT_RESPONSES_OPTION = "global.chat_completions_to_responses_policy"
FALLBACK_SESSION_ID = str(
    uuid.uuid5(uuid.NAMESPACE_URL, "cc-switch:newapi:ch91:jianzhile:omp-fallback")
)
FALLBACK_INSTALLATION_ID = str(
    uuid.uuid5(uuid.NAMESPACE_URL, "cc-switch:newapi:ch91:jianzhile:installation")
)
CODEX_PASSTHROUGH_HEADERS = [
    "Originator",
    "Session-Id",
    "Thread-Id",
    "X-Client-Request-Id",
    "User-Agent",
    "X-Codex-Beta-Features",
    "X-Codex-Turn-State",
    "X-Codex-Turn-Metadata",
    "X-Codex-Window-Id",
    "X-Codex-Parent-Thread-Id",
    "X-OpenAI-Subagent",
    "X-OpenAI-Memgen-Request",
    "X-ResponsesAPI-Include-Timing-Metrics",
    "X-OpenAI-Internal-Codex-Responses-Lite",
]
HEADER_OVERRIDE = {
    "*": "",
    "User-Agent": (
        "codex_exec/0.147.0 (Windows 10.0.26200; x86_64) "
        "WindowsTerminal (codex_exec; 0.147.0)"
    ),
    "Originator": "codex_exec",
    "Session-Id": FALLBACK_SESSION_ID,
    "Thread-Id": FALLBACK_SESSION_ID,
    "X-Client-Request-Id": FALLBACK_SESSION_ID,
    "X-Codex-Beta-Features": "remote_compaction_v2",
    "X-Codex-Turn-Metadata": json.dumps(
        {
            "installation_id": FALLBACK_INSTALLATION_ID,
            "session_id": FALLBACK_SESSION_ID,
            "thread_id": FALLBACK_SESSION_ID,
        },
        separators=(",", ":"),
        sort_keys=True,
    ),
    "X-Codex-Window-Id": f"{FALLBACK_SESSION_ID}:0",
    "X-OpenAI-Internal-Codex-Responses-Lite": "true",
}
PARAM_OVERRIDE = {
    "operations": [
        {
            "mode": "pass_headers",
            "value": CODEX_PASSTHROUGH_HEADERS,
            "keep_origin": False,
            "logic": "AND",
            "conditions": [
                {
                    "path": "request_headers.originator",
                    "mode": "full",
                    "value": "codex_exec",
                }
            ],
        }
    ]
}
CHAT_RESPONSES_MODEL_PATTERNS = [
    r"^gpt-5\.6-sol$",
    r"^zg-gpt-5\.6-sol$",
    r"^zg-agent-gpt-5\.6-sol$",
    r"^jianzhile-codex-gpt-5\.6-sol$",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the transaction; without this flag the command is read-only",
    )
    return parser.parse_args()


def online_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = backup_dir / f"new-api-before-ch91-codex-pass-{stamp}.db"
    if destination.exists():
        raise RuntimeError(f"backup already exists: {destination}")

    source = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        target = sqlite3.connect(destination, timeout=30)
        try:
            source.backup(target)
            result = target.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError(f"backup integrity_check failed: {result}")
        finally:
            target.close()
    finally:
        source.close()
    return destination


def load_smoke():
    path = Path(__file__).with_name("newapi-local-smoke.py")
    spec = importlib.util.spec_from_file_location("newapi_local_smoke_jianzhile", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load newapi-local-smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_channel(connection: sqlite3.Connection) -> sqlite3.Row:
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT id, name, type, status, base_url, models, model_mapping, "
        "header_override, param_override, test_model, auto_ban, priority, weight, "
        "key, channel_info, "
        "typeof(channel_info) AS channel_info_type FROM channels WHERE id = ?",
        (CHANNEL_ID,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"channel {CHANNEL_ID} is missing")
    if row["name"] != CHANNEL_NAME or row["base_url"] != BASE_URL or row["type"] != 1:
        raise RuntimeError(
            f"channel {CHANNEL_ID} identity mismatch: "
            f"name={row['name']!r} type={row['type']} base_url={row['base_url']!r}"
        )
    return row


def desired_values(row: sqlite3.Row) -> tuple[str, str, str, str]:
    models = [item.strip() for item in (row["models"] or "").split(",") if item.strip()]
    if CODEX_MODEL not in models:
        models.append(CODEX_MODEL)

    try:
        mapping = json.loads(row["model_mapping"] or "{}")
    except json.JSONDecodeError as error:
        raise RuntimeError("ch91 model_mapping is invalid JSON") from error
    if not isinstance(mapping, dict):
        raise RuntimeError("ch91 model_mapping must be an object")
    mapping[CODEX_MODEL] = UPSTREAM_MODEL

    return (
        ",".join(models),
        json.dumps(mapping, separators=(",", ":"), sort_keys=True),
        json.dumps(HEADER_OVERRIDE, separators=(",", ":"), sort_keys=True),
        json.dumps(PARAM_OVERRIDE, separators=(",", ":"), sort_keys=True),
    )


def merge_chat_responses_policy(
    current: str, channel_id: int, model_patterns: list[str]
) -> str:
    try:
        policy = json.loads(current)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{CHAT_RESPONSES_OPTION} is invalid JSON") from error
    if not isinstance(policy, dict):
        raise RuntimeError(f"{CHAT_RESPONSES_OPTION} must be a JSON object")

    channel_ids = policy.get("channel_ids") or []
    patterns = policy.get("model_patterns") or []
    if not isinstance(channel_ids, list) or not all(isinstance(item, int) for item in channel_ids):
        raise RuntimeError(f"{CHAT_RESPONSES_OPTION}.channel_ids must be an integer array")
    if not isinstance(patterns, list) or not all(isinstance(item, str) for item in patterns):
        raise RuntimeError(f"{CHAT_RESPONSES_OPTION}.model_patterns must be a string array")

    policy["enabled"] = True
    policy["all_channels"] = False
    policy["channel_ids"] = list(dict.fromkeys([*channel_ids, channel_id]))
    policy["model_patterns"] = list(
        dict.fromkeys([*patterns, *model_patterns])
    )
    return json.dumps(policy, separators=(",", ":"), sort_keys=True)


def desired_chat_responses_policy(current: str) -> str:
    return merge_chat_responses_policy(
        current, CHANNEL_ID, CHAT_RESPONSES_MODEL_PATTERNS
    )


def main() -> int:
    args = parse_args()
    db_path = args.db.expanduser().resolve()
    connection = sqlite3.connect(db_path, timeout=30)
    try:
        row = load_channel(connection)
        models, mapping, headers, param_override = desired_values(row)
        option_row = connection.execute(
            "SELECT value FROM options WHERE key = ?", (CHAT_RESPONSES_OPTION,)
        ).fetchone()
        if option_row is None or not isinstance(option_row[0], str):
            raise RuntimeError(f"{CHAT_RESPONSES_OPTION} is missing")
        original_policy = option_row[0]
        desired_policy = desired_chat_responses_policy(original_policy)
        original_abilities = connection.execute(
            "SELECT `group`, model, enabled, priority, weight FROM abilities "
            "WHERE channel_id = ?",
            (CHANNEL_ID,),
        ).fetchall()
        key_count = len([key for key in (row["key"] or "").splitlines() if key.strip()])
        print(
            f"ch{CHANNEL_ID}: status={row['status']} auto_ban={row['auto_ban']} "
            f"priority={row['priority']} weight={row['weight']} "
            f"keys={key_count} channel_info_type={row['channel_info_type']}"
        )
        print(
            f"desired: alias={CODEX_MODEL} priority={PRIORITY} weight={WEIGHT} "
            "Codex/OMP headers converged, Chat->Responses enabled for ch91"
        )
        if not args.apply:
            print("dry-run only; pass --apply to change the database")
            return 0

        backup = online_backup(db_path)
        print(f"backup ok: {backup.name} ({backup.stat().st_size} bytes, integrity_check=ok)")

        with connection:
            connection.execute(
                "UPDATE channels SET models = ?, model_mapping = ?, header_override = ?, "
                "param_override = ?, test_model = ?, auto_ban = 1, status = 1, "
                "priority = ?, weight = ? WHERE id = ?",
                (
                    models,
                    mapping,
                    headers,
                    param_override,
                    CODEX_MODEL,
                    PRIORITY,
                    WEIGHT,
                    CHANNEL_ID,
                ),
            )
            connection.execute(
                "INSERT INTO abilities (`group`, model, channel_id, enabled, priority, weight) "
                "VALUES ('default', ?, ?, 1, ?, ?) "
                "ON CONFLICT(`group`, model, channel_id) DO UPDATE SET "
                "enabled = 1, priority = excluded.priority, weight = excluded.weight",
                (CODEX_MODEL, CHANNEL_ID, PRIORITY, WEIGHT),
            )
            connection.execute(
                "UPDATE abilities SET enabled = 1, priority = ?, weight = ? "
                "WHERE channel_id = ?",
                (PRIORITY, WEIGHT, CHANNEL_ID),
            )

        smoke = load_smoke()
        token, user_id = smoke.admin_auth()
        api_headers = {
            "Authorization": f"Bearer {token}",
            "New-Api-User": str(user_id),
        }
        option_status, option_body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/option/",
            method="PUT",
            body={"key": CHAT_RESPONSES_OPTION, "value": desired_policy},
            headers=api_headers,
        )
        if (
            option_status != 200
            or not isinstance(option_body, dict)
            or not option_body.get("success")
        ):
            with connection:
                connection.execute(
                    "UPDATE channels SET models = ?, model_mapping = ?, header_override = ?, "
                    "param_override = ?, test_model = ?, auto_ban = ?, status = ?, "
                    "priority = ?, weight = ? WHERE id = ?",
                    (
                        row["models"],
                        row["model_mapping"],
                        row["header_override"],
                        row["param_override"],
                        row["test_model"],
                        row["auto_ban"],
                        row["status"],
                        row["priority"],
                        row["weight"],
                        CHANNEL_ID,
                    ),
                )
                connection.execute(
                    "DELETE FROM abilities WHERE channel_id = ?", (CHANNEL_ID,)
                )
                connection.executemany(
                    "INSERT INTO abilities (`group`, model, channel_id, enabled, priority, weight) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            ability["group"],
                            ability["model"],
                            CHANNEL_ID,
                            ability["enabled"],
                            ability["priority"],
                            ability["weight"],
                        )
                        for ability in original_abilities
                    ],
                )
            raise RuntimeError(f"option API update failed: HTTP {option_status}")

        after = load_channel(connection)
        ability_rows = connection.execute(
            "SELECT model, enabled, priority, weight FROM abilities "
            "WHERE `group` = 'default' AND channel_id = ?",
            (CHANNEL_ID,),
        ).fetchall()
        expected_models = set(models.split(","))
        abilities_ok = (
            {ability["model"] for ability in ability_rows} == expected_models
            and all(
                (ability["enabled"], ability["priority"], ability["weight"])
                == (1, PRIORITY, WEIGHT)
                for ability in ability_rows
            )
        )
        if (
            after["models"] != models
            or after["model_mapping"] != mapping
            or after["header_override"] != headers
            or after["param_override"] != param_override
            or after["test_model"] != CODEX_MODEL
            or after["auto_ban"] != 1
            or after["status"] != 1
            or after["priority"] != PRIORITY
            or after["weight"] != WEIGHT
            or after["channel_info"] != row["channel_info"]
            or after["channel_info_type"] != row["channel_info_type"]
            or not abilities_ok
        ):
            raise RuntimeError("post-transaction readback mismatch")
        option_after = connection.execute(
            "SELECT value FROM options WHERE key = ?", (CHAT_RESPONSES_OPTION,)
        ).fetchone()
        if option_after is None or option_after[0] != desired_policy:
            raise RuntimeError("option readback mismatch")
        print("readback ok: channel_info unchanged; Codex/OMP ability and policy enabled")
        print("wait at least 75s for SyncChannelCache before the E2E probe")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
