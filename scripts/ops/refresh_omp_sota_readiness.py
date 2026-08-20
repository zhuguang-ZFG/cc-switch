#!/usr/bin/env python3
"""Refresh the isolated OMP SOTA route and readiness state once."""

from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
SMOKE_PATH = HERE / "newapi-local-smoke.py"
PROBE_PATH = HERE / "probe_omp_sota_alias.py"
READINESS_PATH = Path.home() / ".omp" / "agent" / "sota-readiness.json"
MODEL = "omp-sota-claude-opus-5"
# 2026-08-20 strict isolation: ch93 carries ONLY the marked alias (plain
# claude-opus-5 removed), so the management probe uses the alias too; the
# channel model_mapping rewrites it to the upstream base model.


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def isolated_channel_id(db_path: Path, model: str = MODEL) -> int | None:
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        rows = connection.execute(
            "SELECT id FROM channels WHERE name LIKE 'omp-sota-%' "
            "AND instr(',' || replace(models, ' ', '') || ',', ?) > 0 "
            "ORDER BY id",
            (f",{model},",),
        ).fetchall()
    if len(rows) > 1:
        raise RuntimeError("multiple isolated SOTA channels expose the same model")
    return int(rows[0][0]) if rows else None


def set_status(smoke: Any, headers: dict[str, str], channel_id: int, status: int) -> bool:
    response_status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}/status",
        method="POST",
        body={"status": status},
        headers=headers,
    )
    return response_status == 200 and isinstance(body, dict) and bool(body.get("success"))


def main() -> int:
    smoke = load_module(SMOKE_PATH, "newapi_local_smoke_for_sota_refresh")
    probe = load_module(PROBE_PATH, "omp_sota_probe_for_refresh")
    db_path = Path(smoke.NEWAPI_DB).resolve()
    channel_id = isolated_channel_id(db_path)
    if channel_id is None:
        print("refresh skipped: isolated SOTA channel unavailable")
        return 1

    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/channel/test/{channel_id}?model={MODEL}",
        headers=headers,
        timeout=90,
    )
    management_ok = status == 200 and isinstance(body, dict) and bool(body.get("success"))
    if not management_ok:
        probe.update_readiness(
            READINESS_PATH,
            f"zg-newapi/{MODEL}",
            channel_id,
            "unavailable",
            "management-probe-failed",
        )
        disabled = set_status(smoke, headers, channel_id, 2)
        print(
            f"refresh unavailable: channelId={channel_id} managementProbe=false "
            f"disabled={str(disabled).lower()}"
        )
        return 1

    if not set_status(smoke, headers, channel_id, 1):
        probe.update_readiness(
            READINESS_PATH,
            f"zg-newapi/{MODEL}",
            channel_id,
            "unavailable",
            "enable-failed",
        )
        print(f"refresh unavailable: channelId={channel_id} enable=false")
        return 1

    try:
        result = subprocess.run(
            [
                sys.executable,
                str(PROBE_PATH),
                "--run",
                "--channel-id",
                str(channel_id),
                "--readiness-path",
                str(READINESS_PATH),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired:
        probe.update_readiness(
            READINESS_PATH,
            f"zg-newapi/{MODEL}",
            channel_id,
            "unavailable",
            "semantic-probe-timeout",
        )
        disabled = set_status(smoke, headers, channel_id, 2)
        print(
            f"refresh unavailable: channelId={channel_id} semanticTimeout=true "
            f"disabled={str(disabled).lower()}"
        )
        return 1
    if result.returncode != 0:
        disabled = set_status(smoke, headers, channel_id, 2)
        print(
            f"refresh unavailable: channelId={channel_id} semanticProbe=false "
            f"disabled={str(disabled).lower()}"
        )
        return 1
    print(f"refresh ready: channelId={channel_id} semanticProbe=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
