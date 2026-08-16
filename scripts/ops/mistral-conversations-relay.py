#!/usr/bin/env python3
"""
OpenAI chat/completions -> Mistral /v1/conversations relay.

Mistral serves glm-5-2 only via its proprietary /v1/conversations API
(agent-style envelope: inputs/completion_args/instructions), not via
/v1/chat/completions (429 there). This relay lets NewAPI treat it as a
plain OpenAI channel:

  NewAPI channel (type=1, base_url=http://127.0.0.1:PORT/v1)
    -> POST /v1/chat/completions (OpenAI shape)
    -> POST https://api.mistral.ai/v1/conversations (Mistral shape)

Conventions mirror codex-relay.py: loopback bind, exclusive Windows port
binding, bounded workers, key from ~/.omp/guardian/secrets.json (never in
repo/logs), log-file rotation.

Known limitations (v1, deliberate):
- stream:true relays upstream SSE deltas live (conversation.response.started/
  message.output.delta/conversation.response.done -> OpenAI chunks); if the
  upstream stream cannot be opened it falls back to buffered + synthesized
  SSE, never worse than the original contract.
- tools/function-calling payloads are NOT translated; requests carrying
  tools are rejected with 400 so callers fail loud instead of silently
  losing capabilities.
- Multimodal content parts: text parts are concatenated; non-text parts
  are rejected with 400.
"""

import argparse
import http.server
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

DEFAULT_PORT = int(os.environ.get("MISTRAL_RELAY_PORT", "16001"))
MAX_REQUEST_BYTES = int(os.environ.get("MISTRAL_RELAY_MAX_REQUEST_BYTES", str(2 * 1024 * 1024)))
MAX_CONCURRENT_REQUESTS = int(os.environ.get("MISTRAL_RELAY_MAX_CONCURRENCY", "16"))
UPSTREAM_TIMEOUT = int(os.environ.get("MISTRAL_RELAY_UPSTREAM_TIMEOUT", "120"))
LOG_ROTATE_BYTES = int(os.environ.get("MISTRAL_RELAY_LOG_ROTATE_BYTES", str(1024 * 1024)))
UPSTREAM = os.environ.get("MISTRAL_RELAY_UPSTREAM", "https://api.mistral.ai/v1/conversations")
DEFAULT_SECRET_NAME = "mistral_glm_key"
SECRET_NAME = os.environ.get("MISTRAL_RELAY_SECRET_NAME", DEFAULT_SECRET_NAME)

SECRETS_FILE = Path.home() / ".omp" / "guardian" / "secrets.json"

# NewAPI-facing model id -> Mistral model id. zai-glm-5-2 is the canonical
# name used in cc-switch/OMP configs; Mistral bills it as glm-5-2.
MODEL_MAP = {"zai-glm-5-2": "glm-5-2"}
MODELS = ["zai-glm-5-2", "glm-5-2"]
DEFAULT_MODEL = "glm-5-2"

KEY = ""
_LOG_HANDLE = None


class RelayError(Exception):
    def __init__(self, status: int, message: str, error_type: str = "relay_error"):
        super().__init__(message)
        self.status = status
        self.message = message
        self.error_type = error_type

    def payload(self) -> dict:
        return {"error": {"message": self.message[:300], "type": self.error_type}}


def load_key(secret_name: str = DEFAULT_SECRET_NAME) -> str:
    scoped_env = f"MISTRAL_RELAY_KEY_{secret_name.upper()}"
    key = os.environ.get(scoped_env, "")
    if not key and secret_name == DEFAULT_SECRET_NAME:
        key = os.environ.get("MISTRAL_RELAY_KEY", "")
    if not key:
        try:
            key = str(json.loads(SECRETS_FILE.read_text(encoding="utf-8-sig")).get(secret_name, ""))
        except (OSError, ValueError):
            key = ""
    if not key:
        raise SystemExit(f"{secret_name} missing (secrets.json or {scoped_env})")
    return key


def configure_output(log_file: str | None) -> None:
    global _LOG_HANDLE
    if not log_file:
        return
    path = Path(log_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > LOG_ROTATE_BYTES:
        with path.open("rb") as source:
            source.seek(-min(path.stat().st_size, LOG_ROTATE_BYTES // 2), os.SEEK_END)
            tail = source.read()
        path.write_bytes(tail)
    _LOG_HANDLE = path.open("a", encoding="utf-8", buffering=1)
    sys.stdout = _LOG_HANDLE
    sys.stderr = _LOG_HANDLE


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def _flatten_text(content) -> str:
    """OpenAI content: str or list of parts. Text parts join; others reject."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(str(part.get("text", "")))
            else:
                raise RelayError(400, "non-text content parts are not supported", "invalid_request")
        return "".join(texts)
    if content is None:
        return ""
    raise RelayError(400, f"unsupported content type: {type(content).__name__}", "invalid_request")


def to_conversations_request(body: dict) -> dict:
    if body.get("tools") or body.get("tool_choice"):
        raise RelayError(400, "tools are not supported by this relay", "invalid_request")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise RelayError(400, "messages must be a non-empty list", "invalid_request")

    instructions: list[str] = []
    inputs: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict):
            raise RelayError(400, "message entries must be objects", "invalid_request")
        role = msg.get("role")
        text = _flatten_text(msg.get("content"))
        if role == "system":
            instructions.append(text)
        elif role in ("user", "assistant"):
            inputs.append({"role": role, "content": text})
        else:
            raise RelayError(400, f"unsupported role: {role!r}", "invalid_request")
    if not inputs:
        raise RelayError(400, "no user/assistant messages to send", "invalid_request")

    completion_args: dict = {}
    for src_key in ("max_tokens", "temperature", "top_p", "stop", "seed"):
        if body.get(src_key) is not None:
            completion_args[src_key] = body[src_key]

    requested = str(body.get("model") or DEFAULT_MODEL)
    out: dict = {
        "model": MODEL_MAP.get(requested, requested),
        "inputs": inputs,
    }
    if completion_args:
        out["completion_args"] = completion_args
    if instructions:
        out["instructions"] = "\n\n".join(instructions)
    return out, requested


def _extract_content(outputs) -> str:
    """Concatenate message.output entries; content may be str or chunk list."""
    texts: list[str] = []
    for entry in outputs or []:
        if not isinstance(entry, dict) or entry.get("type") != "message.output":
            continue
        content = entry.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for chunk in content:
                if isinstance(chunk, dict) and chunk.get("type") == "text":
                    texts.append(str(chunk.get("text", "")))
    return "".join(texts)


def to_openai_response(conv: dict, requested_model: str) -> dict:
    content = _extract_content(conv.get("outputs"))
    usage = conv.get("usage") or {}
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": requested_model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
    }


def sse_stream(completion: dict) -> bytes:
    """Synthesize OpenAI SSE from a buffered completion (kiro_guard pattern)."""
    cid, model = completion["id"], completion["model"]
    content = completion["choices"][0]["message"]["content"]

    def chunk(delta, finish=None, usage=None):
        c = {
            "id": cid, "object": "chat.completion.chunk",
            "created": completion["created"], "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if usage:
            c["usage"] = usage
        return f"data: {json.dumps(c, ensure_ascii=False)}\n\n"

    parts = [
        chunk({"role": "assistant", "content": ""}),
        chunk({"content": content}),
        chunk({}, finish="stop", usage=completion.get("usage")),
        "data: [DONE]\n\n",
    ]
    return "".join(parts).encode("utf-8")


def call_upstream(conv_req: dict) -> dict:
    data = json.dumps(conv_req, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        UPSTREAM, data=data, method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {KEY}",
            "User-Agent": "mistral-conversations-relay/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8")[:300]
        except Exception:  # noqa: BLE001
            detail = ""
        raise RelayError(e.code, f"upstream HTTP {e.code}: {detail}", "upstream_error") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RelayError(502, f"upstream unreachable: {type(e).__name__}", "upstream_unavailable") from None
    except ValueError:
        raise RelayError(502, "upstream returned invalid JSON", "upstream_unavailable") from None

def _openai_chunk(cid: str, created: int, model: str, delta: dict,
                  finish=None, usage=None) -> bytes:
    c = {
        "id": cid, "object": "chat.completion.chunk",
        "created": created, "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage:
        c["usage"] = usage
    return f"data: {json.dumps(c, ensure_ascii=False)}\n\n".encode("utf-8")


def open_upstream_stream(conv_req: dict):
    """Open a streaming /v1/conversations request. Raises RelayError before
    any byte is delivered, so callers can still fall back to buffered mode."""
    conv_req = dict(conv_req, stream=True)
    data = json.dumps(conv_req, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        UPSTREAM, data=data, method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {KEY}",
            "User-Agent": "mistral-conversations-relay/1.0",
        },
    )
    try:
        return urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8")[:300]
        except Exception:  # noqa: BLE001
            detail = ""
        raise RelayError(e.code, f"upstream HTTP {e.code}: {detail}", "upstream_error") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RelayError(502, f"upstream unreachable: {type(e).__name__}", "upstream_unavailable") from None


def iter_openai_sse(resp, requested_model: str):
    """Translate Mistral conversations SSE into OpenAI chat.completion chunks.

    Event contract (probed 2026-08-16):
      conversation.response.started -> role chunk
      message.output.delta {content} -> content chunk
      conversation.response.done {usage} -> finish chunk + [DONE]
    Unknown events are skipped; stream end without a done event still emits
    [DONE] so clients never hang.
    """
    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())
    sent_role = False
    saw_done = False
    event_type = ""
    for raw_line in resp:
        line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            continue
        if line.startswith("event:"):
            event_type = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        try:
            data = json.loads(line[5:].strip())
        except ValueError:
            continue
        if event_type == "conversation.response.started" and not sent_role:
            sent_role = True
            yield _openai_chunk(cid, created, requested_model,
                                {"role": "assistant", "content": ""})
        elif event_type == "message.output.delta":
            content = data.get("content")
            if isinstance(content, list):
                content = "".join(
                    str(c.get("text", "")) for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                )
            if content:
                if not sent_role:
                    sent_role = True
                    yield _openai_chunk(cid, created, requested_model,
                                        {"role": "assistant", "content": ""})
                yield _openai_chunk(cid, created, requested_model,
                                    {"content": str(content)})
        elif event_type == "conversation.response.done":
            saw_done = True
            usage = data.get("usage") or {}
            yield _openai_chunk(cid, created, requested_model, {},
                                finish="stop", usage=usage)
    if not saw_done:
        yield _openai_chunk(cid, created, requested_model, {}, finish="stop")
    yield b"data: [DONE]\n\n"


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "mistral-conversations-relay/1.0"

    def log_message(self, fmt, *args):  # quiet default stderr access log
        return

    def _send_json(self, status: int, payload: dict, extra_headers: dict | None = None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _drain_body(self):
        """Consume any unread request body so keep-alive stays in sync.

        NewAPI's channel test can send a body on requests we answer without
        reading (GET probes, 404 paths); leaving it unread desyncs the
        connection and the next parse reads the body as a request line.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if 0 < length <= MAX_REQUEST_BYTES:
            self.rfile.read(length)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise RelayError(400, f"bad content length: {length}", "invalid_request")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise RelayError(400, "request body is not valid JSON", "invalid_request") from None

    def do_GET(self):
        self._drain_body()
        if self.path.rstrip("/") in ("/health", ""):
            self._send_json(200, {"status": "ok", "upstream": UPSTREAM})
            return
        if self.path.rstrip("/") == "/v1/models":
            now = int(time.time())
            self._send_json(200, {
                "object": "list",
                "data": [
                    {"id": m, "object": "model", "created": now, "owned_by": "mistral-relay"}
                    for m in MODELS
                ],
            })
            return
        self._send_json(404, RelayError(404, f"unknown path: {self.path}", "not_found").payload())

    def do_POST(self):
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._drain_body()
            self._send_json(404, RelayError(404, f"unknown path: {self.path}", "not_found").payload())
            return
        started = time.time()
        try:
            body = self._read_body()
            conv_req, requested_model = to_conversations_request(body)
            if body.get("stream"):
                self._handle_stream(conv_req, requested_model, started)
                return
            conv = call_upstream(conv_req)
            completion = to_openai_response(conv, requested_model)
            usage = completion["usage"]
            log(
                f"ok model={requested_model}->{conv_req['model']} "
                f"tokens={usage['prompt_tokens']}+{usage['completion_tokens']} "
                f"{time.time() - started:.1f}s"
            )
            self._send_json(200, completion)

        except RelayError as e:
            log(f"fail {e.status} {e.message[:200]}")
            self._send_json(e.status, e.payload())
        except Exception as e:  # noqa: BLE001 — relay must never hang a request
            log(f"fail 500 {type(e).__name__}: {e}")
            self._send_json(500, RelayError(500, f"relay internal error: {type(e).__name__}").payload())

    def _handle_stream(self, conv_req: dict, requested_model: str, started: float):
        """True streaming: relay upstream SSE deltas as they arrive.

        Falls back to buffered+synthesized SSE only if the upstream stream
        cannot be opened (RelayError raised before any byte was sent), so
        behavior never regresses below the pre-2026-08-16 contract.
        Connection is closed after [DONE] (no Content-Length on live SSE).
        """
        try:
            resp = open_upstream_stream(conv_req)
        except RelayError as e:
            log(f"stream open failed ({e.status}), falling back to buffered: {e.message[:120]}")
            conv = call_upstream(conv_req)
            completion = to_openai_response(conv, requested_model)
            payload = sse_stream(completion)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        first = True
        try:
            for chunk in iter_openai_sse(resp, requested_model):
                if first:
                    log(f"stream ttft model={requested_model} {time.time() - started:.1f}s")
                    first = False
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            log("stream client disconnected")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # mid-stream failure: SSE already started, close deterministically
            log(f"stream upstream died mid-flight: {type(e).__name__}")
            try:
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except OSError:
                pass
        finally:
            resp.close()


class BoundedThreadingHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32
    # Windows SO_REUSEADDR allows a second process to bind an already-listening
    # port, creating a shadow listener. POSIX keeps fast-rebind semantics.
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, server_address, handler_class, max_workers=MAX_CONCURRENT_REQUESTS):
        self._semaphore = threading.BoundedSemaphore(max_workers)
        super().__init__(server_address, handler_class)

    def process_request(self, request, client_address):
        self._semaphore.acquire()
        try:
            super().process_request(request, client_address)
        except Exception:
            self._semaphore.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._semaphore.release()


def main() -> None:
    global KEY
    parser = argparse.ArgumentParser(description="OpenAI -> Mistral /v1/conversations relay")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--secret-name", default=SECRET_NAME)
    args = parser.parse_args()

    configure_output(args.log_file)
    KEY = load_key(args.secret_name)
    server = BoundedThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    log(f"mistral-conversations-relay on 127.0.0.1:{args.port} -> {UPSTREAM}")
    server.serve_forever()


if __name__ == "__main__":
    main()
