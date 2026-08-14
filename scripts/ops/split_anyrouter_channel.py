"""Back up and convert NewAPI ch72 into a Claude-only recovery domain."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import time
from pathlib import Path
from typing import Any


def load_smoke():
    path = Path(__file__).with_name("newapi-local-smoke.py")
    spec = importlib.util.spec_from_file_location("newapi_local_smoke_anyrouter", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load newapi-local-smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_update(channel: dict[str, Any], smoke: Any) -> dict[str, Any]:
    if channel.get("id") != smoke.ANYROUTER_CHANNEL_ID:
        raise RuntimeError("expected channel id 72")
    if channel.get("name") != "anyrouter":
        raise RuntimeError("channel 72 is not anyrouter")
    key = channel.get("key")
    if not isinstance(key, str) or not key.strip() or "***" in key:
        raise RuntimeError("channel key is empty or masked; refusing PUT")

    updated = {key: value for key, value in channel.items() if key != "status"}
    updated["models"] = ",".join(smoke.ANYROUTER_CLAUDE_MODELS)
    updated["model_mapping"] = json.dumps(
        smoke.ANYROUTER_CLAUDE_MAPPING,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    updated["test_model"] = smoke.ANYROUTER_TEST_MODEL
    updated["priority"] = 40
    updated["weight"] = 2
    updated["auto_ban"] = 0
    return updated


def hydrate_channel_key(channel: dict[str, Any], smoke: Any) -> dict[str, Any]:
    key = channel.get("key")
    if isinstance(key, str) and key.strip() and "***" not in key:
        return channel
    con = sqlite3.connect(f"file:{Path(smoke.NEWAPI_DB).as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT key FROM channels WHERE id = ?",
            (smoke.ANYROUTER_CHANNEL_ID,),
        ).fetchone()
    finally:
        con.close()
    actual = row[0] if row else None
    if not isinstance(actual, str) or not actual.strip():
        raise RuntimeError("channel key unavailable in local SSOT")
    return {**channel, "key": actual}


def safe_summary(channel: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": channel.get("id"),
        "name": channel.get("name"),
        "status": channel.get("status"),
        "priority": channel.get("priority"),
        "weight": channel.get("weight"),
        "auto_ban": channel.get("auto_ban"),
        "test_model": channel.get("test_model"),
        "models": channel.get("models"),
    }


def ability_rows(smoke: Any) -> list[tuple[str, int, int, int]]:
    con = sqlite3.connect(f"file:{Path(smoke.NEWAPI_DB).as_posix()}?mode=ro", uri=True)
    try:
        return list(
            con.execute(
                "SELECT model, enabled, priority, weight FROM abilities "
                "WHERE channel_id = ? ORDER BY model",
                (smoke.ANYROUTER_CHANNEL_ID,),
            )
        )
    finally:
        con.close()


def backup_database(smoke: Any, destination: Path) -> None:
    source = sqlite3.connect(Path(smoke.NEWAPI_DB))
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"database backup integrity check failed: {result}")
    finally:
        target.close()
        source.close()


def verified_posture(channel: dict[str, Any], smoke: Any) -> bool:
    expected_models = set(smoke.ANYROUTER_CLAUDE_MODELS)
    actual_models = {
        model.strip()
        for model in str(channel.get("models") or "").split(",")
        if model.strip()
    }
    try:
        mapping = json.loads(str(channel.get("model_mapping") or "{}"))
    except (TypeError, ValueError):
        return False
    expected_abilities = sorted(
        (model, 0, 40, 2) for model in smoke.ANYROUTER_CLAUDE_MODELS
    )
    return (
        channel.get("status") == 2
        and channel.get("priority") == 40
        and channel.get("weight") == 2
        and channel.get("auto_ban") == 0
        and channel.get("test_model") == smoke.ANYROUTER_TEST_MODEL
        and actual_models == expected_models
        and mapping == smoke.ANYROUTER_CLAUDE_MAPPING
        and ability_rows(smoke) == expected_abilities
    )


def put_channel(smoke: Any, headers: dict[str, str], channel: dict[str, Any]) -> bool:
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/",
        method="PUT",
        body={key: value for key, value in channel.items() if key != "status"},
        headers=headers,
    )
    return status == 200 and isinstance(body, dict) and bool(body.get("success"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{smoke.ANYROUTER_CHANNEL_ID}",
        headers=headers,
    )
    channel = body.get("data") if isinstance(body, dict) else None
    if status != 200 or not isinstance(channel, dict):
        print(f"channel read failed: HTTP {status}")
        return 1
    try:
        channel = hydrate_channel_key(channel, smoke)
        updated = build_update(channel, smoke)
    except RuntimeError as error:
        print(f"refused: {error}")
        return 1

    print("current=" + json.dumps(safe_summary(channel), ensure_ascii=False))
    print(
        "proposed="
        + json.dumps(
            safe_summary({**updated, "status": channel.get("status")}),
            ensure_ascii=False,
        )
    )
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup_dir = Path(smoke.DEPLOY_DIR) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = backup_dir / (
        "channel-72-anyrouter-before-claude-split-"
        f"{stamp}.json"
    )
    db_backup = backup_dir / f"new-api-before-anyrouter-claude-split-{stamp}.db"
    backup.write_text(
        json.dumps({"channel": channel}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    backup_database(smoke, db_backup)
    print(f"channel_backup={backup.name}")
    print(f"database_backup={db_backup.name}")

    if not put_channel(smoke, headers, updated):
        print("update failed; original channel was not intentionally changed")
        return 1

    verify_status, verify_body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{smoke.ANYROUTER_CHANNEL_ID}",
        headers=headers,
    )
    verified = verify_body.get("data") if isinstance(verify_body, dict) else None
    ok = (
        verify_status == 200
        and isinstance(verified, dict)
        and verified_posture(verified, smoke)
    )
    print(f"verified={ok}")
    if ok:
        return 0

    restored = put_channel(smoke, headers, channel)
    print(f"verification failed; rollback_attempted=True restored={restored}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
