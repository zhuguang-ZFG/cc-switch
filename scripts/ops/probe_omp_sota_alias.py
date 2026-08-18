#!/usr/bin/env python3
"""Run a cheap semantic and log-attribution probe for one OMP SOTA alias."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any


SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
DEFAULT_MODEL = "omp-sota-claude-opus-5"
DEFAULT_CHANNEL_ID = 0
EXPECTED_TEXT = "OMP-SOTA-OK"
DEFAULT_PROVIDER = "zg-newapi"
DEFAULT_READINESS_TTL_MS = 15 * 60 * 1000


def load_smoke() -> Any:
    spec = importlib.util.spec_from_file_location(
        "newapi_local_smoke_for_sota_probe", SMOKE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SMOKE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extract_text(body: object) -> str:
    if not isinstance(body, dict):
        return ""
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    return str(content).strip()


def semantic_matches(text: str) -> bool:
    return text in (EXPECTED_TEXT, f"{EXPECTED_TEXT}.")


def latest_log_after(
    db_path: Path, last_id: int, model: str, wait_seconds: float = 5
) -> tuple | None:
    deadline = time.monotonic() + max(0, wait_seconds)
    while time.monotonic() < deadline:
        with closing(
            sqlite3.connect(
                f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
            )
        ) as connection:
            row = connection.execute(
                "SELECT id, channel_id, model_name, is_stream, prompt_tokens, "
                "completion_tokens, use_time FROM logs "
                "WHERE id > ? AND model_name = ? ORDER BY id DESC LIMIT 1",
                (last_id, model),
            ).fetchone()
        if row is not None:
            return tuple(row)
        time.sleep(0.25)
    return None


def verify_log(row: tuple | None, channel_id: int, model: str) -> bool:
    return bool(
        row
        and len(row) == 7
        and row[1] == channel_id
        and row[2] == model
        and row[3] in (0, False)
        and isinstance(row[4], int)
        and row[4] > 0
        and isinstance(row[5], int)
        and row[5] > 0
    )


def update_readiness(
    path: Path,
    selector: str,
    channel_id: int,
    status: str,
    reason: str,
    checked_at_ms: int | None = None,
) -> None:
    if status not in {"ready", "unavailable"}:
        raise ValueError("readiness status must be ready or unavailable")
    checked_at = int(time.time() * 1000) if checked_at_ms is None else checked_at_ms
    payload: dict[str, object] = {
        "schema": 1,
        "ttlMs": DEFAULT_READINESS_TTL_MS,
        "candidates": {},
    }
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                payload.update(existing)
        except (OSError, json.JSONDecodeError):
            pass
    candidates = payload.get("candidates")
    if not isinstance(candidates, dict):
        candidates = {}
    candidates[selector] = {
        "status": status,
        "reason": reason,
        "checkedAt": checked_at,
        "channelId": channel_id,
    }
    payload["schema"] = 1
    payload["ttlMs"] = DEFAULT_READINESS_TTL_MS
    payload["candidates"] = candidates
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--channel-id",
        type=int,
        default=DEFAULT_CHANNEL_ID,
        help="expected NewAPI channel id; 0 discovers an isolated omp-sota channel",
    )
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--readiness-path", type=Path)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not args.model.startswith("omp-sota-"):
        parser.error("--model must use the omp-sota- prefix")
    if args.channel_id < 0:
        parser.error("--channel-id must be non-negative")
    if not args.provider or "/" in args.provider or "\\" in args.provider:
        parser.error("--provider must be a non-empty provider id")
    return args


def discover_channel_id(db_path: Path, model: str) -> int | None:
    """Prefer a dedicated marked channel over shared legacy model pools."""
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        rows = connection.execute(
            "SELECT id, name, status, priority FROM channels "
            "WHERE instr(',' || replace(models, ' ', '') || ',', ?) > 0 "
            "ORDER BY CASE WHEN name LIKE 'omp-sota-%' THEN 0 ELSE 1 END, "
            "status DESC, priority DESC, id",
            (f",{model},",),
        ).fetchall()
    return int(rows[0][0]) if rows else None


def main() -> int:
    args = parse_args()
    if not args.run:
        print(
            f"dry-run: model={args.model} expectedChannel="
            f"{args.channel_id or 'auto'} "
            "maxTokens=8 no request sent"
        )
        return 0

    smoke = load_smoke()
    db_path = Path(smoke.NEWAPI_DB).resolve()
    channel_id = args.channel_id or discover_channel_id(db_path, args.model)
    if channel_id is None:
        print("refused: no NewAPI channel exposes the marked model")
        return 1
    selector = f"{args.provider}/{args.model}"
    token = smoke.read_json(Path(smoke.DEPLOY_DIR) / "client-token.json")
    key = token.get("api_key") or token.get("key")
    if not isinstance(key, str) or not key.strip():
        if args.readiness_path:
            update_readiness(
                args.readiness_path,
                selector,
                channel_id,
                "unavailable",
                "probe-key-unavailable",
            )
        print("refused: client probe key unavailable")
        return 1

    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        last_id = int(
            connection.execute("SELECT COALESCE(MAX(id), 0) FROM logs").fetchone()[0]
        )

    started = time.monotonic()
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/v1/chat/completions",
        method="POST",
        timeout=90,
        headers={"Authorization": f"Bearer {key}"},
        body={
            "model": args.model,
            "max_tokens": 8,
            "messages": [
                {"role": "user", "content": f"Reply only: {EXPECTED_TEXT}."}
            ],
        },
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    text = extract_text(body)
    if status != 200 or not semantic_matches(text):
        if args.readiness_path:
            update_readiness(
                args.readiness_path,
                selector,
                channel_id,
                "unavailable",
                "semantic-failed",
            )
        print(
            f"semantic probe failed: HTTP {status} markerOnly={str(semantic_matches(text)).lower()}"
        )
        return 1

    row = latest_log_after(db_path, last_id, args.model)
    if not verify_log(row, channel_id, args.model):
        if args.readiness_path:
            update_readiness(
                args.readiness_path,
                selector,
                channel_id,
                "unavailable",
                "log-attribution-failed",
            )
        print("log attribution failed: marked model/channel row unavailable")
        return 1
    assert row is not None
    if args.readiness_path:
        update_readiness(
            args.readiness_path,
            selector,
            channel_id,
            "ready",
            "semantic-and-log-verified",
        )
    print(
        f"semantic=ok HTTP=200 model={args.model} channelId={row[1]} "
        f"stream=0 usage={row[4]}/{row[5]} elapsedMs={elapsed_ms} useTime={row[6]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
