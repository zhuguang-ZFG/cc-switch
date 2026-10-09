#!/usr/bin/env python3
"""Read-only probe for the muyuan.do (君の公益) station and its local NewAPI path.

2026-10-07: user supplied a station connection (url + key) and reported the
station's models are not yet wired into OMP. This probe is the evidence step:
enumerate the upstream catalog and/or chat/tool-probe ids end-to-end.

Usage:
  MUYUAN_KEY=... python3 probe_muyuan_station.py                 # upstream catalog
  python3 probe_muyuan_station.py --gateway --chat a,b,c         # local gateway path
  python3 probe_muyuan_station.py --gateway --tools a,b          # tool_calls probe

Keys are never printed or persisted; --gateway reads the OMP zg-newapi key from
~/.omp/agent/models.yml at runtime. No writes anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

UPSTREAM_BASE = os.environ.get("MUYUAN_BASE_URL", "https://muyuan.do")
GATEWAY_BASE = "http://127.0.0.1:3002"
MODELS_YML = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".omp" / "agent" / "models.yml"
RETRY_STATUS = {408, 429, 500, 502, 503, 504}

TOOL_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "probe_echo",
            "description": "Echo a value back. Call this tool with value='ping'.",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        },
    }
]


def read_gateway_key() -> str:
    text = MODELS_YML.read_text(encoding="utf-8")
    block = re.search(r"\n  zg-newapi:\n(.*?)(?=\n  [a-zA-Z][\w.-]*:\n)", text, re.S)
    if block is None:
        raise SystemExit(f"zg-newapi provider block not found in {MODELS_YML}")
    key = re.search(r"apiKey:\s*(sk-\S+)", block.group(1))
    if key is None:
        raise SystemExit("zg-newapi apiKey not found")
    return key.group(1)


def http_json(base: str, key: str, path: str, method: str = "GET", body: dict | None = None, timeout: int = 300):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": "probe/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload: object = json.loads(raw)
        except ValueError:
            payload = raw[:400]
        return exc.code, payload


def _post_chat(base: str, key: str, body: dict, retries: int) -> str:
    started = time.monotonic()
    for attempt in range(retries + 1):
        status, payload = http_json(base, key, "/v1/chat/completions", "POST", body)
        if status in RETRY_STATUS and attempt < retries:
            time.sleep(5)
            continue
        elapsed = time.monotonic() - started
        if isinstance(payload, dict):
            choices = payload.get("choices") or [{}]
            message = choices[0].get("message") or {}
            content = message.get("content") or ""
            reasoning = message.get("reasoning_content") or ""
            usage = payload.get("usage") or {}
            error = payload.get("error") or payload.get("message") or ""
            return (
                f"HTTP {status} {elapsed:.1f}s finish={choices[0].get('finish_reason')!r} "
                f"reply={content[:60]!r} rc_len={len(reasoning)} "
                f"usage={json.dumps(usage, ensure_ascii=False)} err={str(error)[:160]}"
            )
        return f"HTTP {status} {elapsed:.1f}s raw={str(payload)[:200]}"
    raise AssertionError("unreachable")


def chat_probe(base: str, key: str, model_id: str, max_tokens: int, retries: int = 2) -> str:
    return _post_chat(base, key, {
        "model": model_id,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": max_tokens,
        "stream": False,
    }, retries)


def tool_probe(base: str, key: str, model_id: str, retries: int = 2) -> str:
    started = time.monotonic()
    body = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Call the probe_echo tool with value='ping'. Use the tool, do not answer directly."}],
        "tools": TOOL_SCHEMA,
        "tool_choice": "auto",
        "max_tokens": 128,
        "stream": False,
    }
    for attempt in range(retries + 1):
        status, payload = http_json(base, key, "/v1/chat/completions", "POST", body)
        if status in RETRY_STATUS and attempt < retries:
            time.sleep(5)
            continue
        elapsed = time.monotonic() - started
        if isinstance(payload, dict):
            choices = payload.get("choices") or [{}]
            message = choices[0].get("message") or {}
            calls = message.get("tool_calls") or []
            names = [((call.get("function") or {}).get("name") or "?") for call in calls]
            error = payload.get("error") or payload.get("message") or ""
            return (
                f"HTTP {status} {elapsed:.1f}s finish={choices[0].get('finish_reason')!r} "
                f"tool_calls={names} content={(message.get('content') or '')[:40]!r} err={str(error)[:140]}"
            )
        return f"HTTP {status} {elapsed:.1f}s raw={str(payload)[:200]}"
    raise AssertionError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", action="store_true", help="probe the local NewAPI gateway instead of upstream")
    parser.add_argument("--chat", default="", help="comma-separated model ids to chat-probe")
    parser.add_argument("--tools", default="", help="comma-separated model ids to tool-probe")
    parser.add_argument("--max-tokens", type=int, default=16)
    parser.add_argument("--no-list", dest="list", action="store_false")
    args = parser.parse_args()

    if args.gateway:
        base, key, label = GATEWAY_BASE, read_gateway_key(), "gateway"
    else:
        key, label = os.environ.get("MUYUAN_KEY", ""), "upstream"
        base = UPSTREAM_BASE
        if not key:
            print("MUYUAN_KEY missing (or use --gateway)", file=sys.stderr)
            return 2
    print(f"== probe target: {label} ({base}) ==")

    if args.list:
        status, body = http_json(base, key, "/v1/models")
        ids: list[str] = []
        if isinstance(body, dict) and isinstance(body.get("data"), list):
            ids = sorted(str(item.get("id")) for item in body["data"] if isinstance(item, dict))
        print(f"GET /v1/models -> HTTP {status}, {len(ids)} models")
        for model_id in ids:
            print("   ", model_id)

    for model_id in [part.strip() for part in args.chat.split(",") if part.strip()]:
        print(f"chat  {model_id}: {chat_probe(base, key, model_id, args.max_tokens)}")
    for model_id in [part.strip() for part in args.tools.split(",") if part.strip()]:
        print(f"tools {model_id}: {tool_probe(base, key, model_id)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
