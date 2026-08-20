#!/usr/bin/env python3
"""Add a machine-detectable OMP SOTA alias to one existing NewAPI channel.

The default mode is read-only. ``--apply`` creates an integrity-checked online
SQLite backup, updates the channel through NewAPI, and verifies both the
channel projection and the rebuilt ability row. Secrets remain in memory and
are never included in output.

Strict isolation (2026-08-20): the alias may only be added to a dedicated
``omp-sota-*`` channel; adding it to a shared pool is refused because it
silently turns a dedicated-channel outage into paid shared-pool fallback.
``--remove`` remains allowed on any single-key channel as the drift cleanup
path. Note the dedicated ch93 is alias-only, so the add path effectively
applies to future dedicated channels that also carry the base model; ch93
itself is rebuilt by ``create_omp_sota_channel.py``.

Multi-key channels are refused outright: this tool applies changes via API
PUT, which regenerates ``channel_info`` on multi-key channels and wipes manual
DB repairs (t1qq runbook). Multi-key drift must be cleaned by direct DB write
plus a cache-sync wait, as done for ch75 on 2026-08-20.
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
from typing import Any, Callable


SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
ALIAS_PREFIX = "omp-sota-"
DEFAULT_BASE_MODEL = "claude-opus-5"
SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def load_smoke() -> Any:
    spec = importlib.util.spec_from_file_location(
        "newapi_local_smoke_for_omp_sota", SMOKE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_alias(base_model: str, alias: str | None = None) -> str:
    if not SAFE_MODEL_RE.fullmatch(base_model):
        raise ValueError("base model contains unsupported characters")
    expected = f"{ALIAS_PREFIX}{base_model}"
    if alias is not None and alias != expected:
        raise ValueError(f"alias must be exactly {expected}")
    return expected


def parse_models(value: object) -> list[str]:
    models = [item.strip() for item in str(value or "").split(",") if item.strip()]
    if len(models) != len(set(models)):
        raise ValueError("channel models contain duplicates")
    return models


def parse_mapping(value: object) -> dict[str, str]:
    if value in (None, ""):
        return {}
    if not isinstance(value, str):
        raise ValueError("model_mapping must be a JSON string")
    try:
        mapping = json.loads(value)
    except (TypeError, ValueError) as error:
        raise ValueError("model_mapping is invalid JSON") from error
    if not isinstance(mapping, dict) or not all(
        isinstance(key, str) and isinstance(target, str)
        for key, target in mapping.items()
    ):
        raise ValueError("model_mapping must contain string keys and values")
    return mapping


def has_usable_key(channel: dict[str, Any]) -> bool:
    key = channel.get("key")
    return isinstance(key, str) and bool(key.strip()) and "***" not in key


def hydrate_channel_key(
    channel: dict[str, Any], db_path: Path, channel_id: int
) -> dict[str, Any]:
    if has_usable_key(channel):
        return channel
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        row = connection.execute(
            "SELECT key FROM channels WHERE id = ?", (channel_id,)
        ).fetchone()
    key = row[0] if row else None
    if not isinstance(key, str) or not key.strip():
        raise RuntimeError(f"ch{channel_id} key unavailable in local SSOT")
    return {**channel, "key": key}


def is_multi_key_channel(db_path: Path, channel_id: int) -> bool:
    """Read channel_info from the SQLite SSOT (the management API projection
    does not expose multi-key state)."""
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        row = connection.execute(
            "SELECT channel_info FROM channels WHERE id = ?", (channel_id,)
        ).fetchone()
    if not row or not row[0]:
        return False
    try:
        info = json.loads(row[0])
    except (TypeError, ValueError):
        return False
    return bool(isinstance(info, dict) and info.get("is_multi_key"))


def plan_channel_update(
    channel: dict[str, Any],
    channel_id: int,
    base_model: str,
    alias: str | None = None,
    remove: bool = False,
) -> tuple[dict[str, Any], str, bool]:
    marked_alias = build_alias(base_model, alias)
    if channel.get("id") != channel_id:
        raise ValueError(f"expected channel id {channel_id}")
    name = str(channel.get("name") or "")
    # 2026-08-20 strict isolation: the SOTA alias may only live on the
    # dedicated omp-sota-* channel. Adding it to a shared pool (as happened
    # to ch75 on 2026-08-18) silently turns a dedicated-channel outage into
    # paid shared-pool fallback traffic. Removal stays allowed everywhere —
    # it is the drift cleanup path.
    if not remove and not name.startswith(ALIAS_PREFIX):
        raise ValueError(
            f"strict isolation: SOTA alias may only be added to a dedicated "
            f"{ALIAS_PREFIX}* channel, not {name!r}"
        )
    if channel.get("status") != 1:
        # Applies to removal too: verify_projection and the rollback path
        # assume an enabled channel; clean disabled channels by direct DB
        # write instead.
        raise ValueError(f"ch{channel_id} must be enabled before modifying a SOTA alias")
    if not has_usable_key(channel):
        raise ValueError("channel key is empty or masked; refusing PUT")

    models = parse_models(channel.get("models"))
    if base_model not in models:
        raise ValueError(f"ch{channel_id} does not expose base model {base_model}")
    mapping = parse_mapping(channel.get("model_mapping"))
    existing_target = mapping.get(marked_alias)
    if existing_target is not None and existing_target != base_model:
        raise ValueError(
            f"existing alias mapping conflicts: {marked_alias} -> {existing_target}"
        )

    changed = False
    if remove:
        if marked_alias in models:
            models.remove(marked_alias)
            changed = True
        if existing_target is not None:
            del mapping[marked_alias]
            changed = True
    else:
        if marked_alias not in models:
            models.append(marked_alias)
            changed = True
        if existing_target is None:
            mapping[marked_alias] = base_model
            changed = True

    updated = {key: value for key, value in channel.items() if key != "status"}
    updated["models"] = ",".join(models)
    updated["model_mapping"] = json.dumps(
        mapping, ensure_ascii=False, separators=(",", ":")
    )
    return updated, marked_alias, changed


def safe_summary(
    channel: dict[str, Any], alias: str, base_model: str
) -> dict[str, Any]:
    models = parse_models(channel.get("models"))
    mapping = parse_mapping(channel.get("model_mapping"))
    return {
        "id": channel.get("id"),
        "name": channel.get("name"),
        "status": channel.get("status"),
        "priority": channel.get("priority"),
        "weight": channel.get("weight"),
        "baseModelPresent": base_model in models,
        "alias": alias,
        "aliasPresent": alias in models,
        "aliasTarget": mapping.get(alias),
        "modelCount": len(models),
    }


def online_backup(db_path: Path, backup_dir: Path, label: str) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"new-api-before-{label}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    if destination.exists():
        raise RuntimeError(f"backup already exists: {destination.name}")
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as source, closing(sqlite3.connect(destination, timeout=30)) as target:
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()
        if result != ("ok",):
            raise RuntimeError("backup integrity check failed")
    if destination.stat().st_size <= 0:
        raise RuntimeError("backup is empty")
    return destination


def read_abilities(
    db_path: Path, channel_id: int
) -> list[tuple[str, int, int, int]]:
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        return [
            tuple(row)
            for row in connection.execute(
                "SELECT model, enabled, priority, weight FROM abilities "
                "WHERE channel_id = ? ORDER BY model",
                (channel_id,),
            )
        ]


def verify_projection(
    channel: dict[str, Any],
    abilities: list[tuple[str, int, int, int]],
    channel_id: int,
    base_model: str,
    alias: str,
    present: bool = True,
) -> bool:
    if channel.get("id") != channel_id or channel.get("status") != 1:
        return False
    try:
        models = parse_models(channel.get("models"))
        mapping = parse_mapping(channel.get("model_mapping"))
    except ValueError:
        return False
    alias_rows = [row for row in abilities if row[0] == alias]
    if base_model not in models:
        return False
    if not present:
        return alias not in models and alias not in mapping and not alias_rows
    return (
        alias in models
        and mapping.get(alias) == base_model
        and len(alias_rows) == 1
        and alias_rows[0][1] == 1
        and alias_rows[0][2] == channel.get("priority")
        and alias_rows[0][3] == channel.get("weight")
    )


def apply_and_verify(
    original: dict[str, Any],
    updated: dict[str, Any],
    *,
    channel_id: int,
    base_model: str,
    alias: str,
    present: bool = True,
    put_channel: Callable[[dict[str, Any]], bool],
    read_channel: Callable[[], dict[str, Any] | None],
    read_ability_rows: Callable[[], list[tuple[str, int, int, int]]],
) -> dict[str, bool]:
    if not put_channel(updated):
        return {"accepted": False, "verified": False, "rollbackAttempted": False, "restored": False}
    readback = read_channel()
    verified = isinstance(readback, dict) and verify_projection(
        readback, read_ability_rows(), channel_id, base_model, alias, present
    )
    if verified:
        return {"accepted": True, "verified": True, "rollbackAttempted": False, "restored": False}
    original_payload = {key: value for key, value in original.items() if key != "status"}
    restored = put_channel(original_payload)
    return {"accepted": True, "verified": False, "rollbackAttempted": True, "restored": restored}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--channel-id",
        type=int,
        required=True,
        help="existing enabled channel that independently hosts the SOTA key",
    )
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL)
    parser.add_argument("--alias")
    parser.add_argument(
        "--remove", action="store_true", help="remove the exact marked alias"
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.channel_id <= 0:
        parser.error("--channel-id must be positive")
    build_alias(args.base_model, args.alias)
    return args


def main() -> int:
    args = parse_args()
    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

    def read_channel() -> dict[str, Any] | None:
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/{args.channel_id}", headers=headers
        )
        channel = body.get("data") if isinstance(body, dict) else None
        return channel if status == 200 and isinstance(channel, dict) else None

    original = read_channel()
    if original is None:
        print(f"refused: ch{args.channel_id} read failed")
        return 1
    if is_multi_key_channel(db_path, args.channel_id):
        print(
            f"refused: ch{args.channel_id} is multi-key; API PUT regenerates "
            "channel_info and wipes DB repairs — clean alias drift by direct "
            "DB write plus a cache-sync wait (see the t1qq runbook and the "
            "2026-08-20 ch75 cleanup)"
        )
        return 1
    try:
        original = hydrate_channel_key(original, db_path, args.channel_id)
        updated, alias, changed = plan_channel_update(
            original, args.channel_id, args.base_model, args.alias, args.remove
        )
    except (RuntimeError, ValueError) as error:
        print(f"refused: {error}")
        return 1

    proposed = {**updated, "status": original.get("status")}
    print(
        "current="
        + json.dumps(
            safe_summary(original, alias, args.base_model), ensure_ascii=False
        )
    )
    print(
        "proposed="
        + json.dumps(
            safe_summary(proposed, alias, args.base_model), ensure_ascii=False
        )
    )
    if not args.apply:
        print(f"dry-run: changed={str(changed).lower()} no changes made")
        return 0

    if not changed:
        abilities = read_abilities(db_path, args.channel_id)
        verified = verify_projection(
            original,
            abilities,
            args.channel_id,
            args.base_model,
            alias,
            present=not args.remove,
        )
        print(f"idempotent-readback: verified={str(verified).lower()}")
        return 0 if verified else 1

    backup = online_backup(
        db_path,
        Path(smoke.DEPLOY_DIR) / "backups",
        f"omp-sota-ch{args.channel_id}",
    )
    print(f"backup={backup.name} bytes={backup.stat().st_size}")

    def put_channel(payload: dict[str, Any]) -> bool:
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/",
            method="PUT",
            body=payload,
            headers=headers,
        )
        return status == 200 and isinstance(body, dict) and bool(body.get("success"))

    result = apply_and_verify(
        original,
        updated,
        channel_id=args.channel_id,
        base_model=args.base_model,
        alias=alias,
        present=not args.remove,
        put_channel=put_channel,
        read_channel=read_channel,
        read_ability_rows=lambda: read_abilities(db_path, args.channel_id),
    )
    print("result=" + json.dumps(result, separators=(",", ":")))
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
