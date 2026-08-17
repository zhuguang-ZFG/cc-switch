#!/usr/bin/env python3
"""Preflight and enable ch45 as a bounded independent Sol failure domain."""

from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path


CHANNEL_ID = 45
EXPECTED_NAME = "agentrouter"
EXPECTED_BASE_URL = "http://100.83.32.95:8788"
EXPECTED_TEXT = "AGENTROUTER-THIRD-OK"


def load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_channel(db_path: Path) -> tuple:
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        channel = connection.execute(
            "SELECT name, status, priority, weight, typeof(channel_info), base_url "
            "FROM channels WHERE id = ?",
            (CHANNEL_ID,),
        ).fetchone()
        abilities = list(
            connection.execute(
                "SELECT model, enabled, priority, weight FROM abilities "
                "WHERE channel_id = ? ORDER BY model",
                (CHANNEL_ID,),
            )
        )
    if (
        channel is None
        or channel[0] != EXPECTED_NAME
        or channel[4] != "blob"
        or channel[5] != EXPECTED_BASE_URL
    ):
        raise RuntimeError("ch45 identity, base_url, or channel_info contract drift")
    if channel[2:4] != (40, 5):
        raise RuntimeError(f"ch45 posture is {channel[2]}/{channel[3]}, expected 40/5")
    if not abilities or any(row[2:4] != (40, 5) for row in abilities):
        raise RuntimeError("ch45 ability posture drift")
    return tuple(channel), [tuple(row) for row in abilities]


def set_status(smoke, headers: dict[str, str], status: int) -> None:
    response_status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{CHANNEL_ID}/status",
        method="POST",
        body={"status": status},
        headers=headers,
    )
    if response_status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"ch45 status={status} failed: HTTP {response_status}")


def omp_semantic_probe() -> None:
    result = subprocess.run(
        (
            "omp", "-p", "--no-session", "--no-tools", "--thinking", "off",
            "--hide-thinking", "--model", "agentrouter/gpt-5.6-sol",
            "--max-time", "3m", f"Reply with exactly: {EXPECTED_TEXT}",
        ),
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=190,
        check=False,
    )
    if result.returncode != 0 or result.stdout.strip() != EXPECTED_TEXT:
        raise RuntimeError("AgentRouter OMP semantic preflight failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--cache-wait", type=int, default=75)
    args = parser.parse_args()
    if args.cache_wait < 0:
        parser.error("--cache-wait must not be negative")

    smoke = load_module("agentrouter_rollout_smoke", "newapi-local-smoke.py")
    posture = load_module("agentrouter_rollout_posture", "update_zzzcoding_sol_primary.py")
    db_path = Path(smoke.NEWAPI_DB).resolve()
    original, abilities = read_channel(db_path)
    print(
        f"ch45 {original[0]}: status={original[1]} posture=40/5 "
        f"abilities={len(abilities)}"
    )
    if not args.apply:
        print("dry-run: no probes run and no channel status changed")
        return 0

    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}
    for _ in range(2):
        posture.management_probe(smoke, headers, CHANNEL_ID)
    omp_semantic_probe()
    backup = posture.online_backup(
        db_path, prefix="new-api-before-agentrouter-sol-rollout"
    )
    changed = False
    try:
        if original[1] != 1:
            set_status(smoke, headers, 1)
            changed = True
            time.sleep(args.cache_wait)
        readback, readback_abilities = read_channel(db_path)
        if readback[1] != 1 or any(row[1] != 1 for row in readback_abilities):
            raise RuntimeError("ch45 enabled readback or abilities failed")
    except BaseException:
        if changed:
            set_status(smoke, headers, int(original[1]))
            time.sleep(args.cache_wait)
        restored, restored_abilities = read_channel(db_path)
        if restored != original or restored_abilities != abilities:
            raise RuntimeError(f"ch45 rollback failed; backup={backup.name}")
        raise

    print(
        f"OK: ch45 enabled at p40/w5 after 2 management + OMP semantic probes; "
        f"backup={backup.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
