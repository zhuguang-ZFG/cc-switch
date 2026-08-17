#!/usr/bin/env python3
"""Read-only end-to-end verification for the zzzcoding Sol primary."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import time
import urllib.request
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
POSTURE_PATH = Path(__file__).with_name("update_zzzcoding_sol_primary.py")
EXPECTED_TEXT = "CH92-PRIMARY-OK"


@dataclass(frozen=True)
class ProbeMetrics:
    status: int
    text: str
    done: bool
    prompt_tokens: int
    completion_tokens: int
    header_ms: int
    ttft_ms: int | None
    max_semantic_gap_ms: int
    total_ms: int


def parse_sse_data(data: str) -> tuple[str, bool, int, int]:
    if data == "[DONE]":
        return "", True, 0, 0
    event = json.loads(data)
    fragment = ""
    choices = event.get("choices") or []
    if choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") or choice.get("message") or {}
        content = delta.get("content") if isinstance(delta, dict) else None
        if isinstance(content, str):
            fragment = content
    if event.get("type") == "response.output_text.delta":
        delta = event.get("delta")
        if isinstance(delta, str):
            fragment = delta
    usage = event.get("usage") or {}
    response = event.get("response")
    if isinstance(response, dict) and isinstance(response.get("usage"), dict):
        usage = response["usage"]
    prompt_tokens = 0
    completion_tokens = 0
    if isinstance(usage, dict):
        prompt_tokens = int(
            usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        )
        completion_tokens = int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
    return fragment, False, prompt_tokens, completion_tokens


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_chat_sse(payload: bytes) -> tuple[str, bool, int, int]:
    fragments: list[str] = []
    done = False
    prompt_tokens = 0
    completion_tokens = 0
    for raw_line in payload.decode("utf-8", errors="replace").splitlines():
        if not raw_line.startswith("data:"):
            continue
        data = raw_line[5:].strip()
        if data == "[DONE]":
            done = True
            continue
        if not data:
            continue
        fragment, event_done, event_prompt, event_completion = parse_sse_data(data)
        fragments.append(fragment)
        done = done or event_done
        prompt_tokens = max(prompt_tokens, event_prompt)
        completion_tokens = max(completion_tokens, event_completion)
    return "".join(fragments).strip(), done, prompt_tokens, completion_tokens


def read_incremental_sse(
    response: BinaryIO, *, status: int, started: float, header_ms: int
) -> ProbeMetrics:
    fragments: list[str] = []
    done = False
    prompt_tokens = 0
    completion_tokens = 0
    first_semantic: float | None = None
    previous_semantic: float | None = None
    max_gap_ms = 0
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace")
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data:
            continue
        now = time.monotonic()
        fragment, event_done, event_prompt, event_completion = parse_sse_data(data)
        done = done or event_done
        prompt_tokens = max(prompt_tokens, event_prompt)
        completion_tokens = max(completion_tokens, event_completion)
        if fragment:
            fragments.append(fragment)
            if first_semantic is None:
                first_semantic = now
            if previous_semantic is not None:
                max_gap_ms = max(max_gap_ms, int((now - previous_semantic) * 1000))
            previous_semantic = now
    finished = time.monotonic()
    return ProbeMetrics(
        status=status,
        text="".join(fragments).strip(),
        done=done,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        header_ms=header_ms,
        ttft_ms=(
            int((first_semantic - started) * 1000)
            if first_semantic is not None
            else None
        ),
        max_semantic_gap_ms=max_gap_ms,
        total_ms=int((finished - started) * 1000),
    )


def read_client_key(smoke) -> str:
    payload = smoke.read_json(Path(smoke.DEPLOY_DIR) / "client-token.json")
    key = payload.get("api_key") or payload.get("key")
    if not isinstance(key, str) or not key.strip():
        raise RuntimeError("local client token is missing")
    return key.strip()


def aggregate_probe_metrics(
    smoke,
    key: str,
    *,
    expected_text: str = EXPECTED_TEXT,
    model: str = "gpt-5.6-sol",
    prompt: str | None = None,
    max_tokens: int = 32,
    tools: list[dict] | None = None,
    timeout: int = 90,
) -> ProbeMetrics:
    body = {
        "model": model,
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": max_tokens,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": prompt or f"Reply with exactly: {expected_text}",
            }
        ],
    }
    if tools is not None:
        body["tools"] = tools
        body["tool_choice"] = "none"
    request = urllib.request.Request(
        f"{smoke.NEWAPI_BASE}/v1/chat/completions",
        method="POST",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = response.status
        header_ms = int((time.monotonic() - started) * 1000)
        metrics = read_incremental_sse(
            response, status=status, started=started, header_ms=header_ms
        )
    if status != 200:
        raise RuntimeError(f"aggregate probe failed: HTTP {status}")
    return metrics


def aggregate_probe(smoke, key: str) -> tuple[str, bool, int, int, int]:
    metrics = aggregate_probe_metrics(smoke, key)
    return (
        metrics.text,
        metrics.done,
        metrics.prompt_tokens,
        metrics.completion_tokens,
        metrics.total_ms,
    )


def latest_log_after(
    db_path: Path, last_id: int, *, wait_seconds: float = 5
) -> tuple | None:
    if wait_seconds < 0:
        raise ValueError("wait_seconds must not be negative")
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        with closing(
            sqlite3.connect(
                f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30
            )
        ) as connection:
            row = connection.execute(
                "SELECT id, channel_id, channel_name, model_name, is_stream, "
                "prompt_tokens, completion_tokens, use_time "
                "FROM logs WHERE id > ? AND model_name = 'gpt-5.6-sol' "
                "ORDER BY id DESC LIMIT 1",
                (last_id,),
            ).fetchone()
        if row is not None:
            return tuple(row)
        time.sleep(0.25)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-pending-posture",
        action="store_true",
        help="verify identities and ch92 semantics before the p55 migration",
    )
    args = parser.parse_args()
    smoke = load_module("newapi_local_smoke_for_zzzcoding_verify", SMOKE_PATH)
    posture = load_module("zzzcoding_posture_for_verify", POSTURE_PATH)
    db_path = Path(smoke.NEWAPI_DB).resolve()

    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        if args.allow_pending_posture:
            state = posture.read_state(connection)
        else:
            posture.verify_targets(connection)
            state = posture.read_state(connection)
        last_id = int(connection.execute("SELECT COALESCE(MAX(id), 0) FROM logs").fetchone()[0])
    print(
        "posture readback: "
        f"ch92={state['channels'][92][2]}/{state['channels'][92][3]}, "
        f"ch91={state['channels'][91][2]}/{state['channels'][91][3]}, "
        f"ch83={state['channels'][83][2]}/{state['channels'][83][3]} "
        f"status={state['channels'][83][1]}, abilities=ok"
    )

    token, user_id = smoke.admin_auth()
    headers = {
        "Authorization": f"Bearer {token}",
        "New-Api-User": str(user_id),
    }
    for attempt in range(1, 3):
        started = time.monotonic()
        posture.management_probe(smoke, headers)
        print(
            f"ch92 management Responses probe {attempt}/2: ok "
            f"({int((time.monotonic() - started) * 1000)}ms)"
        )

    metrics = aggregate_probe_metrics(smoke, read_client_key(smoke))
    if metrics.text != EXPECTED_TEXT:
        raise RuntimeError(f"aggregate semantic mismatch: {metrics.text!r}")
    if not metrics.done:
        raise RuntimeError("aggregate stream ended without [DONE]")
    if metrics.prompt_tokens <= 0 or metrics.completion_tokens <= 0:
        raise RuntimeError(
            "aggregate usage invalid: "
            f"prompt={metrics.prompt_tokens} completion={metrics.completion_tokens}"
        )

    log_row = latest_log_after(db_path, last_id)
    if log_row is None:
        raise RuntimeError("fresh aggregate request is missing from NewAPI logs")
    log_id, channel_id, channel_name, model_name, is_stream, log_prompt, log_completion, use_time = log_row
    if channel_id != 92 or model_name != "gpt-5.6-sol" or not is_stream:
        raise RuntimeError(
            "aggregate log attribution mismatch: "
            f"channel={channel_id} model={model_name} stream={is_stream}"
        )
    if log_prompt <= 0 or log_completion <= 0:
        raise RuntimeError(
            f"aggregate log usage invalid: prompt={log_prompt} completion={log_completion}"
        )
    print(
        f"aggregate semantic probe: {metrics.text}, HTTP 200, SSE [DONE], "
        f"usage={metrics.prompt_tokens}/{metrics.completion_tokens}, "
        f"headers={metrics.header_ms}ms, ttft={metrics.ttft_ms}ms, "
        f"max_gap={metrics.max_semantic_gap_ms}ms, total={metrics.total_ms}ms"
    )
    print(
        f"log attribution: id={log_id}, channel_id=92, "
        f"channel={channel_name}, stream=1, use_time={use_time}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
