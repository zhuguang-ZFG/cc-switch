#!/usr/bin/env python3
"""Measure forced-channel Sol context boundaries with redacted evidence."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
import time
import urllib.error
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


LADDER = (200_000, 280_000, 340_000, 380_000, 396_000)
BOUNDARY_WIDTH = 16_000
CALIBRATION_TOLERANCE = 1_000
PAYLOAD_OVERHEAD_ESTIMATE = 4_400
TOOL_OFFSET = 32_000
CHANNELS = {
    92: "zzzcoding-codex-gpt-5.6-sol",
    91: "jianzhile-codex-gpt-5.6-sol",
}
CONTEXT_MARKERS = (
    "context length",
    "context_length",
    "maximum context",
    "max context",
    "too many tokens",
    "token limit",
    "input is too long",
    "request too large",
)
SIMPLE_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "noop",
            "description": "Return without side effects.",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


def load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def classify_http_error(status: int, body: str) -> str:
    lowered = body.lower()
    if status in (400, 413, 422) and any(marker in lowered for marker in CONTEXT_MARKERS):
        return "context_limit"
    if status in (408, 429) or status >= 500:
        return "upstream_or_gateway"
    return "request_rejected"


def filler_prompt(units: int, expected_text: str) -> str:
    return (
        f"Reply with exactly: {expected_text}\n"
        "Ignore the filler below.\nFILLER:\n"
        + ("x " * max(1, units))
    )


def tool_targets(last_passing_tokens: int) -> tuple[int, ...]:
    lower = max(1, last_passing_tokens - TOOL_OFFSET)
    return (lower, last_passing_tokens) if lower != last_passing_tokens else (lower,)


def run_channel_campaign(
    probe: Callable[[int, str], dict], emit: Callable[[dict], None]
) -> dict:
    last_success: dict | None = None
    failing_target: int | None = None
    status = "complete"
    for target in LADDER:
        outcome = probe(target, "plain")
        emit(outcome)
        if outcome["success"]:
            last_success = outcome
            continue
        if outcome["category"] != "context_limit":
            status = "inconclusive"
            break
        repeated = probe(target, "plain-recheck")
        emit(repeated)
        if repeated["success"]:
            last_success = repeated
            continue
        if repeated["category"] != "context_limit":
            status = "inconclusive"
            break
        failing_target = target
        status = "bounded"
        break

    boundary: dict[str, int] | None = None
    if failing_target is not None and last_success is not None:
        low = int(last_success["prompt_tokens"])
        high = failing_target
        while high - low > BOUNDARY_WIDTH:
            midpoint = (low + high) // 2
            outcome = probe(midpoint, "binary")
            emit(outcome)
            if outcome["success"]:
                last_success = outcome
                measured = int(outcome["prompt_tokens"])
                if measured <= low:
                    status = "inconclusive"
                    break
                low = measured
            elif outcome["category"] == "context_limit":
                high = midpoint
            else:
                status = "inconclusive"
                break
        boundary = {"last_success": low, "first_failure": high}

    tool_status = "skipped"
    if last_success is not None and status != "inconclusive":
        tool_status = "complete"
        for target in tool_targets(int(last_success["prompt_tokens"])):
            tool_outcome = probe(target, "tool")
            emit(tool_outcome)
            if not tool_outcome["success"]:
                if tool_outcome["category"] == "context_limit":
                    tool_status = "context_limited"
                else:
                    tool_status = "inconclusive"
                    status = "inconclusive"

    return {
        "status": status,
        "last_passing_prompt_tokens": (
            int(last_success["prompt_tokens"]) if last_success is not None else None
        ),
        "boundary": boundary,
        "tool_status": tool_status,
    }


def append_jsonl(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def max_log_id(db_path: Path) -> int:
    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        return int(
            connection.execute("SELECT COALESCE(MAX(id), 0) FROM logs").fetchone()[0]
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="send the bounded campaign")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--channel",
        action="append",
        type=int,
        choices=sorted(CHANNELS),
        help="limit a rerun to one forced channel (repeatable)",
    )
    args = parser.parse_args()
    if args.timeout < 30 or args.timeout > 600:
        parser.error("--timeout must be between 30 and 600 seconds")

    output = args.output or Path.cwd() / (
        ".tmp-sol-context-campaign-" + time.strftime("%Y%m%d-%H%M%S") + ".jsonl"
    )
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    selected_channels = args.channel or list(CHANNELS)
    print(
        f"campaign: channels={','.join(str(item) for item in selected_channels)} "
        "ladder=200k,280k,340k,380k,396k "
        f"boundary=+/-{BOUNDARY_WIDTH // 2} tokens output={output.name}"
    )
    if not args.run:
        print("dry-run: no model requests sent")
        return 0

    verifier = load_module("sol_context_verifier", "verify_zzzcoding_sol_primary.py")
    smoke = load_module("sol_context_smoke", "newapi-local-smoke.py")
    key = verifier.read_client_key(smoke)
    db_path = Path(smoke.NEWAPI_DB).resolve()
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    summaries: dict[int, dict] = {}

    for channel_id in selected_channels:
        model = CHANNELS[channel_id]
        sequence = 0

        def emit(record: dict, *, _channel_id=channel_id) -> None:
            nonlocal sequence
            sequence += 1
            append_jsonl(
                output,
                {
                    "timestamp": timestamp,
                    "channel_id": _channel_id,
                    "sequence": sequence,
                    **record,
                },
            )

        def probe(target: int, shape: str, *, _model=model, _channel_id=channel_id) -> dict:
            units = max(1, target - PAYLOAD_OVERHEAD_ESTIMATE)
            attempts = 0
            while True:
                attempts += 1
                expected = f"CTX-{_channel_id}-{shape.upper()}-OK"
                last_id = max_log_id(db_path)
                try:
                    metrics = verifier.aggregate_probe_metrics(
                        smoke,
                        key,
                        expected_text=expected,
                        model=_model,
                        prompt=filler_prompt(units, expected),
                        max_tokens=8,
                        tools=SIMPLE_TOOL if shape == "tool" else None,
                        timeout=args.timeout,
                    )
                    log_row = verifier.latest_log_after(
                        db_path, last_id, wait_seconds=30
                    )
                    attributed = log_row is not None and int(log_row[1]) == _channel_id
                    semantic_ok = metrics.text == expected and metrics.done
                    success = bool(
                        semantic_ok
                        and attributed
                        and metrics.prompt_tokens > 0
                        and metrics.completion_tokens > 0
                    )
                    outcome = {
                        "phase": shape,
                        "target_prompt_tokens": target,
                        "payload_units": units,
                        "calibration_attempts": attempts,
                        "success": success,
                        "category": "ok" if success else "semantic_or_attribution",
                        "http_status": metrics.status,
                        "prompt_tokens": metrics.prompt_tokens,
                        "completion_tokens": metrics.completion_tokens,
                        "header_ms": metrics.header_ms,
                        "ttft_ms": metrics.ttft_ms,
                        "max_semantic_gap_ms": metrics.max_semantic_gap_ms,
                        "total_ms": metrics.total_ms,
                        "done": metrics.done,
                        "semantic_ok": semantic_ok,
                        "attributed_channel_id": (
                            int(log_row[1]) if log_row else None
                        ),
                        "log_id": int(log_row[0]) if log_row else None,
                    }
                    delta = target - metrics.prompt_tokens
                    if (
                        outcome["success"]
                        and abs(delta) > CALIBRATION_TOLERANCE
                        and attempts < 3
                        and metrics.prompt_tokens > 0
                    ):
                        units = max(1, int(units * target / metrics.prompt_tokens))
                        continue
                    if outcome["success"] and abs(delta) > CALIBRATION_TOLERANCE:
                        outcome["success"] = False
                        outcome["category"] = "calibration_out_of_tolerance"
                    return outcome
                except urllib.error.HTTPError as error:
                    raw = error.read(16_384).decode("utf-8", errors="replace")
                    return {
                        "phase": shape,
                        "target_prompt_tokens": target,
                        "payload_units": units,
                        "calibration_attempts": attempts,
                        "success": False,
                        "category": classify_http_error(error.code, raw),
                        "http_status": error.code,
                        "prompt_tokens": 0,
                    }
                except Exception as error:
                    return {
                        "phase": shape,
                        "target_prompt_tokens": target,
                        "payload_units": units,
                        "calibration_attempts": attempts,
                        "success": False,
                        "category": "transport_or_parser",
                        "error_type": type(error).__name__,
                        "http_status": None,
                        "prompt_tokens": 0,
                    }

        summary = run_channel_campaign(probe, emit)
        summaries[channel_id] = summary
        append_jsonl(
            output,
            {
                "timestamp": timestamp,
                "channel_id": channel_id,
                "record_type": "summary",
                **summary,
            },
        )

    print(json.dumps({"output": str(output), "channels": summaries}, sort_keys=True))
    return 1 if any(item["status"] == "inconclusive" for item in summaries.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
