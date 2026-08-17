#!/usr/bin/env python3
"""Run one redacted Sol semantic/latency monitor sample without mutation."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


EXPECTED_TEXT = "SOL-MONITOR-OK"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )
    os.replace(temporary, path)


def read_failure_count(path: Path) -> tuple[int, bool]:
    if not path.exists():
        return 0, False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        count = int(value.get("consecutive_failures", 0))
        if count < 0:
            raise ValueError("negative count")
        return count, False
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0, True


def next_failure_state(
    previous: int, *, ok: bool, timestamp: str, state_invalid: bool = False
) -> tuple[dict, bool]:
    count = 0 if ok else previous + 1
    state = {
        "consecutive_failures": count,
        "last_result": "ok" if ok else "failed",
        "updated_at": timestamp,
    }
    if state_invalid:
        state["previous_state_invalid"] = True
    return state, count >= 2


def max_log_id(db_path: Path) -> int:
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        return int(
            connection.execute("SELECT COALESCE(MAX(id), 0) FROM logs").fetchone()[0]
        )


def append_jsonl(path: Path, value: dict) -> None:
    with path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.home() / ".omp" / "guardian" / "sol-semantic-monitor",
    )
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    if args.timeout < 10 or args.timeout > 240:
        parser.error("--timeout must be between 10 and 240 seconds")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "state.json"
    results_path = output_dir / "results.jsonl"
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record: dict[str, object] = {
        "timestamp": timestamp,
        "model": "gpt-5.6-sol",
        "expected_channel_id": 92,
    }
    ok = False
    try:
        base = Path(__file__).resolve().parent
        verifier = load_module("sol_monitor_verifier", base / "verify_zzzcoding_sol_primary.py")
        smoke = load_module("sol_monitor_smoke", base / "newapi-local-smoke.py")
        db_path = Path(smoke.NEWAPI_DB).resolve()
        last_id = max_log_id(db_path)
        metrics = verifier.aggregate_probe_metrics(
            smoke,
            verifier.read_client_key(smoke),
            expected_text=EXPECTED_TEXT,
            timeout=args.timeout,
        )
        log_row = verifier.latest_log_after(db_path, last_id)
        channel_id = int(log_row[1]) if log_row else None
        log_id = int(log_row[0]) if log_row else None
        ok = bool(
            metrics.status == 200
            and metrics.text == EXPECTED_TEXT
            and metrics.done
            and metrics.prompt_tokens > 0
            and metrics.completion_tokens > 0
            and channel_id == 92
        )
        record.update(
            {
                "status": metrics.status,
                "semantic_ok": metrics.text == EXPECTED_TEXT,
                "done": metrics.done,
                "prompt_tokens": metrics.prompt_tokens,
                "completion_tokens": metrics.completion_tokens,
                "header_ms": metrics.header_ms,
                "ttft_ms": metrics.ttft_ms,
                "max_semantic_gap_ms": metrics.max_semantic_gap_ms,
                "total_ms": metrics.total_ms,
                "channel_id": channel_id,
                "log_id": log_id,
            }
        )
    except Exception as error:
        record.update(
            {
                "status": None,
                "semantic_ok": False,
                "done": False,
                "error_type": type(error).__name__,
            }
        )

    previous, state_invalid = read_failure_count(state_path)
    state, alert = next_failure_state(
        previous, ok=ok, timestamp=timestamp, state_invalid=state_invalid
    )
    atomic_json(state_path, state)
    record.update(
        {
            "ok": ok,
            "consecutive_failures": state["consecutive_failures"],
            "alert": alert,
            "previous_state_invalid": state_invalid,
        }
    )
    append_jsonl(results_path, record)
    print(json.dumps(record, ensure_ascii=True, sort_keys=True))
    return 1 if alert else 0


if __name__ == "__main__":
    raise SystemExit(main())
