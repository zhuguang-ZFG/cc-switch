#!/usr/bin/env python3
"""
codex-relay.py — OpenAI chat/completions -> zzzcoding Codex backend relay.

The zzzcoding Sub2API gateway only accepts the official Codex CLI fingerprint
(X-Codex-* headers + full codex request body). This relay replays that exact
request shape against https://api.zzzcoding.org/responses and converts the
SSE stream to the OpenAI chat.completion format so the local NewAPI can host
it as a normal channel.

Usage:
    python codex-relay.py [--port 15999]

Config (in ~/.omp/guardian/secrets.json):
    zzzcoding_codex_key   - the Sub2API key (env override CODEX_RELAY_KEY)

Template: codex-relay-template.json (captured from the real Codex CLI 0.146.0).
"""

import http.server
import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

PORT = int(os.environ.get("CODEX_RELAY_PORT", "15999"))
UPSTREAM = "https://api.zzzcoding.org/responses"
CLI_UA = "codex_exec/0.146.0 (Windows 10.0.26200; x86_64) WindowsTerminal (codex_exec; 0.146.0)"
HERE = Path(__file__).resolve().parent
TEMPLATE_FILE = HERE / "codex-relay-template.json"
MODELS = [
    "codex-auto-review", "gpt-5.3-codex-spark", "gpt-5.4", "gpt-5.4-mini",
    "gpt-5.5", "gpt-5.6", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra",
    "gpt-image-1", "gpt-image-1.5", "gpt-image-2",
]

SECRETS_FILE = Path.home() / ".omp" / "guardian" / "secrets.json"
INSTALL_FILE = Path.home() / ".omp" / "guardian" / "codex-relay-install-id"


def load_key() -> str:
    key = os.environ.get("CODEX_RELAY_KEY", "")
    if not key:
        try:
            key = str(json.loads(SECRETS_FILE.read_text(encoding="utf-8")).get("zzzcoding_codex_key", ""))
        except (OSError, ValueError):
            key = ""
    if not key:
        raise SystemExit("zzzcoding_codex_key missing (secrets.json or CODEX_RELAY_KEY)")
    return key


def load_installation_id() -> str:
    """Stable device identity: the gateway correlates codex clients by
    installation id; a fresh random id per request is flagged (502)."""
    try:
        iid = INSTALL_FILE.read_text(encoding="utf-8").strip()
        if iid:
            return iid
    except OSError:
        pass
    iid = str(uuid.uuid4())
    try:
        INSTALL_FILE.write_text(iid, encoding="utf-8")
    except OSError:
        pass
    return iid


KEY = load_key()
INSTALL_ID = load_installation_id()
TEMPLATE = json.loads(TEMPLATE_FILE.read_text(encoding="utf-8"))


def fingerprint_headers(sid: str) -> dict:
    return {
        "User-Agent": CLI_UA,
        "Accept": "text/event-stream",
        "Authorization": f"Bearer {KEY}",
        "Content-Type": "application/json",
        "Originator": "codex_exec",
        "Session-Id": sid,
        "Thread-Id": sid,
        "X-Client-Request-Id": sid,
        "X-Codex-Beta-Features": "remote_compaction_v2",
        "X-Codex-Turn-Metadata": json.dumps({
            "installation_id": INSTALL_ID, "session_id": sid, "thread_id": sid,
        }),
        "X-Codex-Window-Id": f"{sid}:0",
    }


def build_body(model: str, user_prompt: str) -> dict:
    body = json.loads(json.dumps(TEMPLATE))  # deep copy
    body["model"] = model
    sid = str(uuid.uuid4())
    body["prompt_cache_key"] = sid
    meta = body["client_metadata"]
    meta["turn_id"] = sid
    meta["thread_id"] = sid
    meta["session_id"] = sid
    meta["x-codex-installation-id"] = INSTALL_ID
    meta["x-codex-window-id"] = f"{sid}:0"
    meta["x-codex-turn-metadata"] = json.dumps({
        "installation_id": INSTALL_ID, "session_id": sid, "thread_id": sid,
    }, ensure_ascii=False)
    for item in body["input"]:
        item["id"] = "msg_" + sid
        if item.get("role") == "user":
            item["content"][0]["text"] = user_prompt
    return body, sid


def call_upstream(body: dict, sid: str):
    req = urllib.request.Request(
        UPSTREAM, data=json.dumps(body).encode("utf-8"),
        headers=fingerprint_headers(sid), method="POST",
    )
    ctx = ssl.create_default_context()
    return urllib.request.urlopen(req, timeout=600, context=ctx)


def parse_sse_line(line: str):
    line = line.strip()
    if not line or line.startswith(":"):
        return None
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if data == "[DONE]":
        return {"__done__": True}
    try:
        return json.loads(data)
    except ValueError:
        return None


def handle_chat(wfile, body, stream: bool) -> bytes:
    """Streaming: emits SSE chunks to wfile, returns b"". Non-stream: returns
    the final JSON bytes (caller sends with Content-Length)."""
    model = body.get("model", "gpt-5.5")
    messages = body.get("messages") or []
    user_prompt = messages[-1].get("content", "") if messages else ""
    if isinstance(user_prompt, list):
        user_prompt = " ".join(
            seg.get("text", "") for seg in user_prompt if isinstance(seg, dict)
        )
    codex_body, sid = build_body(model, user_prompt)

    def emit_json(obj: dict):
        payload = f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"
        wfile.write(payload.encode("utf-8"))
        wfile.flush()

    try:
        upstream = call_upstream(codex_body, sid)
    except urllib.error.HTTPError as e:
        detail = e.read(300).decode("utf-8", "ignore")
        err = {"error": {"message": f"upstream {e.code}: {detail[:200]}", "type": "upstream_error"}}
        return json.dumps(err, ensure_ascii=False).encode("utf-8")
    except Exception as e:  # noqa: BLE001
        err = {"error": {"message": f"relay upstream call failed: {e}", "type": "relay_error"}}
        return json.dumps(err, ensure_ascii=False).encode("utf-8")

    resp_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
    created = int(time.time())
    content_parts = []
    usage = None
    error = None
    finished = False
    buf = b""
    try:
        while not finished:
            chunk = upstream.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                ev = parse_sse_line(text)
                if ev is None:
                    continue
                if ev.get("__done__"):
                    finished = True
                    break
                if ev.get("type") == "response.output_text.delta":
                    delta = ev.get("delta", "")
                    content_parts.append(delta)
                    if stream:
                        emit_json({
                            "id": resp_id, "object": "chat.completion.chunk",
                            "created": created, "model": model,
                            "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
                        })
                elif ev.get("type") == "response.completed":
                    r = ev.get("response") or {}
                    u = r.get("usage") or {}
                    usage = {
                        "prompt_tokens": u.get("input_tokens", 0),
                        "completion_tokens": u.get("output_tokens", 0),
                        "total_tokens": u.get("input_tokens", 0) + u.get("output_tokens", 0),
                    }
                    finished = True
                    break
                elif ev.get("type") == "response.failed" or ev.get("type") == "error":
                    r = ev.get("response") or ev
                    e = r.get("error") or {}
                    error = e.get("message", ev.get("message", "codex upstream failed"))
                    finished = True
                    break
    finally:
        upstream.close()

    if error:
        return json.dumps({"error": {"message": str(error)[:300], "type": "upstream_error"}}, ensure_ascii=False).encode("utf-8")

    text = "".join(content_parts)
    if stream:
        emit_json({
            "id": resp_id, "object": "chat.completion.chunk",
            "created": created, "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        })
        wfile.write(b"data: [DONE]\n\n")
        wfile.flush()
        return b""
    out = {
        "id": resp_id, "object": "chat.completion",
        "created": created, "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": usage,
    }
    return json.dumps(out, ensure_ascii=False).encode("utf-8")


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, code: int, obj: dict):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("?")[0] == "/v1/models":
            self._json(200, {"object": "list", "data": [
                {"id": m, "object": "model", "owned_by": "zzzcoding"}
                for m in MODELS
            ]})
        else:
            self._json(404, {"error": {"message": "not found", "type": "invalid_request_error"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            self._json(400, {"error": {"message": "invalid json", "type": "invalid_request_error"}})
            return
        path = self.path.split("?")[0]
        if path == "/v1/chat/completions":
            stream = bool(body.get("stream", False))
            result = handle_chat(self.wfile, body, stream)
            if not stream:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(result)))
                self.end_headers()
                self.wfile.write(result)
        else:
            self._json(404, {"error": {"message": "not found", "type": "invalid_request_error"}})

    def log_message(self, fmt, *args):  # quiet
        pass


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"codex-relay on 127.0.0.1:{PORT}")
    server.serve_forever()
