#!/usr/bin/env python3
"""
OpenAI chat/completions -> zzzcoding Codex backend relay.

The upstream only accepts the official Codex CLI request fingerprint. This
service keeps the captured Codex request envelope while translating the
caller's conversation, tools, and Responses SSE events at the boundary.
"""

import argparse
import copy
import http.server
import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


DEFAULT_PORT = int(os.environ.get("CODEX_RELAY_PORT", "15999"))
MAX_REQUEST_BYTES = int(os.environ.get("CODEX_RELAY_MAX_REQUEST_BYTES", str(2 * 1024 * 1024)))
MAX_CONCURRENT_REQUESTS = int(os.environ.get("CODEX_RELAY_MAX_CONCURRENCY", "16"))
UPSTREAM_TIMEOUT = int(os.environ.get("CODEX_RELAY_UPSTREAM_TIMEOUT", "60"))
SEMANTIC_TIMEOUT = int(os.environ.get("CODEX_RELAY_SEMANTIC_TIMEOUT", "60"))
LOG_ROTATE_BYTES = int(os.environ.get("CODEX_RELAY_LOG_ROTATE_BYTES", str(1024 * 1024)))
DEFAULT_UPSTREAM = "https://api.zzzcoding.org/responses"
DEFAULT_SECRET_NAME = "zzzcoding_codex_key"
UPSTREAM = os.environ.get("CODEX_RELAY_UPSTREAM", DEFAULT_UPSTREAM)
SECRET_NAME = os.environ.get("CODEX_RELAY_SECRET_NAME", DEFAULT_SECRET_NAME)
CLI_UA = "codex_exec/0.146.0 (Windows 10.0.26200; x86_64) WindowsTerminal (codex_exec; 0.146.0)"
CLI_VERSION = "0.146.0"
HERE = Path(__file__).resolve().parent
TEMPLATE_FILE = HERE / "codex-relay-template.json"
MODELS = [
    "codex-auto-review", "gpt-5.3-codex-spark", "gpt-5.4", "gpt-5.4-mini",
    "gpt-5.5", "gpt-5.6", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra",
]

SECRETS_FILE = Path.home() / ".omp" / "guardian" / "secrets.json"
INSTALL_FILE = Path.home() / ".omp" / "guardian" / "codex-relay-install-id"
TEMPLATE = json.loads(TEMPLATE_FILE.read_text(encoding="utf-8"))
KEY = ""
INSTALL_ID = ""
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
    scoped_env = f"CODEX_RELAY_KEY_{secret_name.upper()}"
    key = os.environ.get(scoped_env, "")
    if not key and secret_name == DEFAULT_SECRET_NAME:
        key = os.environ.get("CODEX_RELAY_KEY", "")
    if not key:
        try:
            key = str(json.loads(SECRETS_FILE.read_text(encoding="utf-8-sig")).get(secret_name, ""))
        except (OSError, ValueError):
            key = ""
    if not key:
        raise SystemExit(f"{secret_name} missing (secrets.json or {scoped_env})")
    return key


def load_installation_id() -> str:
    try:
        installation_id = INSTALL_FILE.read_text(encoding="utf-8").strip()
        if installation_id:
            return installation_id
    except OSError:
        pass
    installation_id = str(uuid.uuid4())
    try:
        INSTALL_FILE.parent.mkdir(parents=True, exist_ok=True)
        INSTALL_FILE.write_text(installation_id, encoding="utf-8")
    except OSError:
        pass
    return installation_id


def configure_runtime(upstream: str = UPSTREAM, secret_name: str = SECRET_NAME) -> None:
    global KEY, INSTALL_ID, UPSTREAM, SECRET_NAME
    parsed = urllib.parse.urlsplit(upstream)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise SystemExit("--upstream must be an HTTPS URL without userinfo")
    if not secret_name or not secret_name.replace("_", "").isalnum():
        raise SystemExit("--secret-name must contain only letters, digits, and underscores")
    UPSTREAM = upstream.rstrip("/")
    SECRET_NAME = secret_name
    KEY = load_key(secret_name)
    INSTALL_ID = load_installation_id()


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


def fingerprint_headers(session_id: str) -> dict:
    if not KEY or not INSTALL_ID:
        raise RelayError(500, "relay runtime is not configured")
    return {
        "User-Agent": CLI_UA,
        "Accept": "text/event-stream",
        "Authorization": f"Bearer {KEY}",
        "Content-Type": "application/json",
        "Originator": "codex_cli_rs",
        "Version": CLI_VERSION,
        "Session-Id": session_id,
        "Thread-Id": session_id,
        "X-Client-Request-Id": session_id,
        "X-Codex-Beta-Features": "remote_compaction_v2",
        "X-Codex-Turn-Metadata": json.dumps({
            "installation_id": INSTALL_ID,
            "session_id": session_id,
            "thread_id": session_id,
        }),
        "X-Codex-Window-Id": f"{session_id}:0",
    }


def _content_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return json.dumps(content, ensure_ascii=False)

    parts = []
    for segment in content:
        if not isinstance(segment, dict):
            parts.append(str(segment))
            continue
        text = segment.get("text")
        if isinstance(text, str):
            parts.append(text)
        elif segment.get("type") in {"image_url", "input_image"}:
            parts.append("[image supplied by caller]")
    return "\n".join(part for part in parts if part)


def format_conversation(messages) -> tuple[str, str]:
    if not isinstance(messages, list):
        raise RelayError(400, "messages must be an array", "invalid_request_error")

    instruction_parts = []
    conversation_parts = []
    for message in messages:
        if not isinstance(message, dict):
            raise RelayError(400, "each message must be an object", "invalid_request_error")
        role = str(message.get("role") or "user").lower()
        content = _content_text(message.get("content"))
        if role in {"system", "developer"}:
            if content:
                instruction_parts.append(content)
            continue

        label = role.upper()
        if role == "tool":
            call_id = message.get("tool_call_id") or "unknown"
            label = f"TOOL RESULT {call_id}"
        if content:
            conversation_parts.append(f"[{label}]\n{content}")

        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function") or {}
            if not isinstance(function, dict):
                continue
            conversation_parts.append(
                "[ASSISTANT TOOL CALL]\n"
                f"name={function.get('name', '')}\n"
                f"arguments={function.get('arguments', '')}"
            )

    return "\n\n".join(instruction_parts), "\n\n".join(conversation_parts)


def translate_tools(tools) -> list[dict]:
    if tools is None:
        return []
    if not isinstance(tools, list):
        raise RelayError(400, "tools must be an array", "invalid_request_error")

    translated = []
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        function = tool.get("function") or {}
        if not isinstance(function, dict) or not function.get("name"):
            continue
        translated.append({
            "type": "function",
            "name": str(function["name"]),
            "description": str(function.get("description") or ""),
            "strict": bool(function.get("strict", False)),
            "parameters": function.get("parameters") or {"type": "object", "properties": {}},
        })
    return translated


def translate_tool_choice(tool_choice):
    if tool_choice is None:
        return "auto"
    if isinstance(tool_choice, str) and tool_choice in {"auto", "none", "required"}:
        return tool_choice
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        function = tool_choice.get("function") or {}
        if isinstance(function, dict) and function.get("name"):
            return {"type": "function", "name": str(function["name"])}
    raise RelayError(400, "unsupported tool_choice", "invalid_request_error")


def build_body(
    model: str,
    messages,
    tools=None,
    tool_choice=None,
    parallel_tool_calls=None,
) -> tuple[dict, str]:
    body = copy.deepcopy(TEMPLATE)
    session_id = str(uuid.uuid4())
    extra_instructions, conversation = format_conversation(messages)
    body["model"] = model
    body["prompt_cache_key"] = session_id
    body["stream"] = True
    if extra_instructions:
        body["instructions"] = f"{body.get('instructions', '')}\n\nCaller instructions:\n{extra_instructions}"

    # Captured input messages describe the capture session's sandbox, cwd, and
    # approval policy. They are runtime state, not part of the client fingerprint.
    body["input"] = [{
        "type": "message",
        "id": f"msg_{session_id}_0",
        "role": "user",
        "content": [{"type": "input_text", "text": conversation}],
    }]

    translated_tools = translate_tools(tools)
    if translated_tools:
        body["tools"] = translated_tools
        body["tool_choice"] = translate_tool_choice(tool_choice)
        if parallel_tool_calls is not None:
            body["parallel_tool_calls"] = bool(parallel_tool_calls)
    else:
        body.pop("tools", None)
        body.pop("tool_choice", None)
        body.pop("parallel_tool_calls", None)

    metadata = body.setdefault("client_metadata", {})
    metadata["turn_id"] = session_id
    metadata["thread_id"] = session_id
    metadata["session_id"] = session_id
    metadata["x-codex-installation-id"] = INSTALL_ID
    metadata["x-codex-window-id"] = f"{session_id}:0"
    metadata["x-codex-turn-metadata"] = json.dumps({
        "installation_id": INSTALL_ID,
        "session_id": session_id,
        "thread_id": session_id,
    }, ensure_ascii=False)
    return body, session_id


def call_upstream(body: dict, session_id: str):
    request = urllib.request.Request(
        UPSTREAM,
        data=json.dumps(body).encode("utf-8"),
        headers=fingerprint_headers(session_id),
        method="POST",
    )
    return urllib.request.urlopen(request, timeout=UPSTREAM_TIMEOUT, context=ssl.create_default_context())


def parse_sse_line(line: str):
    line = line.strip()
    if not line or line.startswith(":") or not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if data == "[DONE]":
        return {"__done__": True}
    try:
        return json.loads(data)
    except ValueError:
        return None


def _upstream_error(error: Exception) -> RelayError:
    if isinstance(error, urllib.error.HTTPError):
        detail = error.read(300).decode("utf-8", "ignore")
        return RelayError(error.code, f"upstream {error.code}: {detail[:200]}", "upstream_error")
    if isinstance(error, RelayError):
        return error
    return RelayError(502, f"relay upstream call failed: {error}", "upstream_error")


def handle_chat(wfile, body: dict, stream: bool, start_stream=None) -> bytes:
    if not isinstance(body, dict):
        raise RelayError(400, "request body must be an object", "invalid_request_error")
    if stream and start_stream is None:
        raise RelayError(500, "stream response callback is missing")
    model = str(body.get("model") or "gpt-5.5")
    codex_body, session_id = build_body(
        model,
        body.get("messages") or [],
        body.get("tools"),
        body.get("tool_choice"),
        body.get("parallel_tool_calls"),
    )
    try:
        upstream = call_upstream(codex_body, session_id)
    except Exception as error:
        raise _upstream_error(error) from error

    response_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
    created = int(time.time())
    content_parts = []
    tool_calls = {}
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    completed = False
    stream_started = False
    semantic_deadline = time.monotonic() + SEMANTIC_TIMEOUT
    buffer = b""

    def emit_json(obj: dict):
        wfile.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8"))
        wfile.flush()

    def emit_delta(delta: dict, finish_reason=None):
        emit_json({
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        })

    def ensure_stream_started():
        nonlocal stream_started
        if not stream or stream_started:
            return
        start_stream()
        stream_started = True
        emit_delta({"role": "assistant", "content": ""})

    def upsert_tool(index: int, item: dict) -> dict:
        record = tool_calls.setdefault(index, {
            "id": item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:12]}",
            "type": "function",
            "function": {"name": item.get("name") or "", "arguments": item.get("arguments") or ""},
        })
        if item.get("name"):
            record["function"]["name"] = item["name"]
        if item.get("arguments") and not record["function"]["arguments"]:
            record["function"]["arguments"] = item["arguments"]
        return record

    try:
        try:
            while not completed:
                chunk = upstream.read(4096)
                if not chunk:
                    break
                if stream and not stream_started and time.monotonic() > semantic_deadline:
                    raise RelayError(504, "upstream produced no semantic output before deadline", "timeout_error")
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    event = parse_sse_line(raw.decode("utf-8", "replace"))
                    if event is None:
                        continue
                    if event.get("__done__"):
                        completed = True
                        break
                    event_type = event.get("type")
                    if event_type == "response.output_text.delta":
                        delta = str(event.get("delta") or "")
                        content_parts.append(delta)
                        if stream:
                            ensure_stream_started()
                            emit_delta({"content": delta})
                    elif event_type == "response.output_item.added":
                        item = event.get("item") or {}
                        if item.get("type") == "function_call":
                            index = int(event.get("output_index") or 0)
                            record = upsert_tool(index, item)
                            if stream:
                                ensure_stream_started()
                                emit_delta({"tool_calls": [{
                                    "index": index,
                                    "id": record["id"],
                                    "type": "function",
                                    "function": {"name": record["function"]["name"], "arguments": ""},
                                }]})
                    elif event_type == "response.function_call_arguments.delta":
                        index = int(event.get("output_index") or 0)
                        delta = str(event.get("delta") or "")
                        record = upsert_tool(index, {})
                        record["function"]["arguments"] += delta
                        if stream:
                            ensure_stream_started()
                            emit_delta({"tool_calls": [{"index": index, "function": {"arguments": delta}}]})
                    elif event_type == "response.output_item.done":
                        item = event.get("item") or {}
                        if item.get("type") == "function_call":
                            upsert_tool(int(event.get("output_index") or 0), item)
                    elif event_type == "response.completed":
                        response = event.get("response") or {}
                        upstream_usage = response.get("usage") or {}
                        usage = {
                            "prompt_tokens": int(upstream_usage.get("input_tokens") or 0),
                            "completion_tokens": int(upstream_usage.get("output_tokens") or 0),
                            "total_tokens": int(upstream_usage.get("input_tokens") or 0) + int(upstream_usage.get("output_tokens") or 0),
                        }
                        completed = True
                        break
                    elif event_type in {"response.failed", "error"}:
                        response = event.get("response") or event
                        error = response.get("error") or {}
                        message = str(error.get("message") or event.get("message") or "codex upstream failed")
                        relay_error = RelayError(502, message, "upstream_error")
                        if stream_started:
                            emit_json(relay_error.payload())
                            wfile.write(b"data: [DONE]\n\n")
                            wfile.flush()
                            return b""
                        raise relay_error
        except TimeoutError as error:
            relay_error = RelayError(504, "upstream timed out before semantic output", "timeout_error")
            if stream_started:
                emit_json(relay_error.payload())
                wfile.write(b"data: [DONE]\n\n")
                wfile.flush()
                return b""
            raise relay_error from error
    finally:
        upstream.close()

    if not completed:
        error = RelayError(502, "upstream stream ended before completion", "upstream_error")
        if stream_started:
            emit_json(error.payload())
            wfile.write(b"data: [DONE]\n\n")
            wfile.flush()
            return b""
        raise error

    finish_reason = "tool_calls" if tool_calls else "stop"
    if stream:
        ensure_stream_started()
        emit_delta({}, finish_reason)
        wfile.write(b"data: [DONE]\n\n")
        wfile.flush()
        return b""

    message = {"role": "assistant", "content": "".join(content_parts) or None}
    if tool_calls:
        message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
    result = {
        "id": response_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": usage,
    }
    return json.dumps(result, ensure_ascii=False).encode("utf-8")


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _json(self, code: int, obj: dict):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def _start_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in {"/v1/models", "/models"}:
            self._json(200, {"object": "list", "data": [
                {"id": model, "object": "model", "owned_by": "zzzcoding"}
                for model in MODELS
            ]})
        elif path in {"", "/health"}:
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"error": {"message": "not found", "type": "invalid_request_error"}})

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path not in {"/v1/chat/completions", "/chat/completions"}:
            self._json(404, {"error": {"message": "not found", "type": "invalid_request_error"}})
            return

        length_header = self.headers.get("Content-Length")
        try:
            length = int(length_header) if length_header is not None else -1
        except ValueError:
            length = -1
        if length < 0:
            self._json(411, {"error": {"message": "Content-Length is required", "type": "invalid_request_error"}})
            return
        if length > MAX_REQUEST_BYTES:
            self.close_connection = True
            self._json(413, {"error": {"message": "request body is too large", "type": "invalid_request_error"}})
            return

        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._json(400, {"error": {"message": "invalid json", "type": "invalid_request_error"}})
            return

        stream = bool(body.get("stream", False)) if isinstance(body, dict) else False
        stream_started = False

        def start_stream():
            nonlocal stream_started
            self._start_sse()
            stream_started = True

        try:
            result = handle_chat(self.wfile, body, stream, start_stream)
            if not stream:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(result)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(result)
                self.close_connection = True
        except RelayError as error:
            if stream_started:
                self.wfile.write(f"data: {json.dumps(error.payload())}\n\ndata: [DONE]\n\n".encode("utf-8"))
                self.wfile.flush()
                self.close_connection = True
            else:
                self._json(error.status, error.payload())
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as error:
            print(f"codex-relay internal error: {type(error).__name__}", file=sys.stderr, flush=True)
            if not stream_started:
                self._json(500, {"error": {"message": "relay internal error", "type": "relay_error"}})
            self.close_connection = True

    def log_message(self, _fmt, *args):
        status = args[1] if len(args) > 1 else "-"
        path = self.path.split("?", 1)[0]
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"{timestamp} {self.client_address[0]} {self.command} {path} {status}", file=sys.stderr, flush=True)


class BoundedThreadingHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, server_address, handler_class, max_workers=MAX_CONCURRENT_REQUESTS):
        super().__init__(server_address, handler_class)
        self._worker_slots = threading.BoundedSemaphore(max_workers)

    def process_request(self, request, client_address):
        self._worker_slots.acquire()
        try:
            super().process_request(request, client_address)
        except Exception:
            self._worker_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Codex-only upstream OpenAI-compatible relay")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--log-file", default=os.environ.get("CODEX_RELAY_LOG_FILE"))
    parser.add_argument("--upstream", default=UPSTREAM)
    parser.add_argument("--secret-name", default=SECRET_NAME)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")
    configure_output(args.log_file)
    configure_runtime(args.upstream, args.secret_name)
    server = BoundedThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"codex-relay on 127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
