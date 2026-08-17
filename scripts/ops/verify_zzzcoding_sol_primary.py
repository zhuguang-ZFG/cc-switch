#!/usr/bin/env python3
"""Read-only end-to-end verification for the zzzcoding Sol primary."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import time
import urllib.request
from contextlib import closing
from pathlib import Path


SMOKE_PATH = Path(__file__).with_name("newapi-local-smoke.py")
POSTURE_PATH = Path(__file__).with_name("update_zzzcoding_sol_primary.py")
EXPECTED_TEXT = "CH92-PRIMARY-OK"


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
        event = json.loads(data)
        choices = event.get("choices") or []
        if choices:
            choice = choices[0] if isinstance(choices[0], dict) else {}
            delta = choice.get("delta") or choice.get("message") or {}
            content = delta.get("content") if isinstance(delta, dict) else None
            if isinstance(content, str):
                fragments.append(content)
        usage = event.get("usage") or {}
        if isinstance(usage, dict):
            prompt_tokens = max(
                prompt_tokens,
                int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
            )
            completion_tokens = max(
                completion_tokens,
                int(
                    usage.get("completion_tokens")
                    or usage.get("output_tokens")
                    or 0
                ),
            )
    return "".join(fragments).strip(), done, prompt_tokens, completion_tokens


def read_client_key(smoke) -> str:
    payload = smoke.read_json(Path(smoke.DEPLOY_DIR) / "client-token.json")
    key = payload.get("api_key") or payload.get("key")
    if not isinstance(key, str) or not key.strip():
        raise RuntimeError("local client token is missing")
    return key.strip()


def aggregate_probe(smoke, key: str) -> tuple[str, bool, int, int, int]:
    body = {
        "model": "gpt-5.6-sol",
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": 32,
        "messages": [
            {
                "role": "user",
                "content": f"Reply with exactly: {EXPECTED_TEXT}",
            }
        ],
    }
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
    with urllib.request.urlopen(request, timeout=90) as response:
        status = response.status
        payload = response.read()
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if status != 200:
        raise RuntimeError(f"aggregate probe failed: HTTP {status}")
    text, done, prompt_tokens, completion_tokens = parse_chat_sse(payload)
    return text, done, prompt_tokens, completion_tokens, elapsed_ms


def latest_log_after(db_path: Path, last_id: int) -> tuple | None:
    deadline = time.monotonic() + 5
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
    smoke = load_module("newapi_local_smoke_for_zzzcoding_verify", SMOKE_PATH)
    posture = load_module("zzzcoding_posture_for_verify", POSTURE_PATH)
    db_path = Path(smoke.NEWAPI_DB).resolve()

    with closing(
        sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    ) as connection:
        posture.verify_targets(connection)
        last_id = int(connection.execute("SELECT COALESCE(MAX(id), 0) FROM logs").fetchone()[0])
    print("posture readback: ch92=60/15 primary, ch91=50/5 backup, abilities=ok")

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

    text, done, prompt_tokens, completion_tokens, elapsed_ms = aggregate_probe(
        smoke, read_client_key(smoke)
    )
    if text != EXPECTED_TEXT:
        raise RuntimeError(f"aggregate semantic mismatch: {text!r}")
    if not done:
        raise RuntimeError("aggregate stream ended without [DONE]")
    if prompt_tokens <= 0 or completion_tokens <= 0:
        raise RuntimeError(
            f"aggregate usage invalid: prompt={prompt_tokens} completion={completion_tokens}"
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
        f"aggregate semantic probe: {text}, HTTP 200, SSE [DONE], "
        f"usage={prompt_tokens}/{completion_tokens}, elapsed={elapsed_ms}ms"
    )
    print(
        f"log attribution: id={log_id}, channel_id=92, "
        f"channel={channel_name}, stream=1, use_time={use_time}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
