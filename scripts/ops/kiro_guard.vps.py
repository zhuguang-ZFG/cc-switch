#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kiro-guard: kiro 反代断流守卫（Anthropic /v1/messages 前置缓冲层）

原理：kiro 反代对大 payload 流式响应会截断，但 stream:false 能返回完整 JSON。
本服务接收 new-api 的流式请求 → 改 stream:false 打上游 → 校验完整性 →
客户端要流式就把完整消息合成为 SSE 发回。

默认 KIRO_GUARD_TEE=1：改为 tee 模式——上游 stream:true 实时转发 SSE，
边转边重组 message 做 classify，终止事件扣到判定后才发；文本截断时
用 continuation 请求把流续上（低 TTFT，同时保留守卫判定）。

硬失败 / 软截断耗尽重试 → 502（或已开 SSE 时 error 事件），让 new-api 切渠。
软可疑：同上游再打（默认 1 次，带退避；对齐 Kiro-Go #143）。

只监听 127.0.0.1，鉴权头透传（不存 key）。

Community ports (P0 2026-07-26):
- soft retry backoff ~700ms (Kiro-Go #142/#143)
- empty/truncated tool_use input (kiro-gateway #56)
- soft_* JSONL journal for ops (analyze_newapi_dx)
- content-block / keyword filter → immediate 502 (AgentRouter edge filter;
  do NOT soft-retry; NewAPI needs 502 to switch off AR)
- Cyrillic-Bypass (community marko1olo/agentrouter-setup-guide): latin c→с
  in system/message text before AR; decode on response. AR units only.
"""
from __future__ import annotations

import collections
import copy
import gzip as _gzip
import io
import json
import re
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple

LISTEN_PORT = int(os.environ.get("KIRO_GUARD_PORT", "8400"))
UPSTREAM = os.environ.get("KIRO_GUARD_UPSTREAM", "https://k40.shengqainbang.cn")
TIMEOUT = int(os.environ.get("KIRO_GUARD_TIMEOUT", "300"))
SOFT_RETRY = int(os.environ.get("KIRO_GUARD_SOFT_RETRY", "1"))
# Kiro-Go #143: immediate cross-endpoint retries fail together; ~700ms recovered 5/5.
SOFT_RETRY_BACKOFF_MS = int(os.environ.get("KIRO_GUARD_SOFT_RETRY_BACKOFF_MS", "700"))
SHORT_OUT_TOKENS = int(os.environ.get("KIRO_GUARD_SHORT_OUT", "32"))
SHORT_IN_TOKENS = int(os.environ.get("KIRO_GUARD_SHORT_IN", "2000"))
SHORT_MAX_TOKENS_GATE = int(os.environ.get("KIRO_GUARD_SHORT_MAX_TOKENS", "256"))
TEXT_HEUR = os.environ.get("KIRO_GUARD_TEXT_HEUR", "1") not in ("0", "false", "False")
INTENT_MAX_CHARS = int(os.environ.get("KIRO_GUARD_INTENT_MAX_CHARS", "900"))
JOURNAL_PATH = os.environ.get(
    "KIRO_GUARD_JOURNAL", "/opt/new-api/kiro-guard-soft.jsonl"
)
# Explicit upstream proxy (agentrouter / some 100x exits). Prefer KIRO_GUARD_PROXY;
# fall back to HTTPS_PROXY / HTTP_PROXY for existing systemd units.
PROXY = (
    os.environ.get("KIRO_GUARD_PROXY")
    or os.environ.get("HTTPS_PROXY")
    or os.environ.get("https_proxy")
    or os.environ.get("HTTP_PROXY")
    or os.environ.get("http_proxy")
    or ""
).strip()
# CLIProxyAPIPlus / kiro-gateway spirit: after soft retries, rewrite truncated
# tool_use into a Bash echo so Claude Code keeps the agent loop (not fake end_turn).
SOFT_LIMIT = os.environ.get("KIRO_GUARD_SOFT_LIMIT", "1") not in ("0", "false", "False")
SOFT_LIMIT_REASONS = frozenset(
    {"empty_tool_input", "tool_use_no_block", "tool_incomplete", "tool_missing_name"}
)
SOFT_LIMIT_MSG = (
    "SOFT_LIMIT_REACHED: upstream truncated a tool call (empty/incomplete input). "
    "Do NOT claim the write succeeded. Split into smaller chunks "
    "(max ~80-300 lines per Write); re-read the path, Write the first chunk, "
    "then Edit/append the rest."
)
# AgentRouter (and similar) edge content filters: remap to 502 so NewAPI
# switches channel immediately. Soft-retrying the same blocked prompt is useless.
CONTENT_BLOCK_FAILOVER = os.environ.get(
    "KIRO_GUARD_CONTENT_BLOCK_FAILOVER", "1"
) not in ("0", "false", "False")
_CONTENT_BLOCK_MARKERS = (
    "sensitive_words_detected",
    "sensitive_words",
    "contentblocked",
    "content_blocked",
    "content blocked",
    "content_policy",
    "content filter",
    "content_filter",
    "prompt blocked",
    "request blocked",
    "敏感词",
    "违规内容",
    "不允许的内容",
    "内容审核",
    "内容风控",
)
# Community Cyrillic-Bypass for AgentRouter sensitive_words WAF.
# Default OFF — enable only on kiro-guard-ar-* via systemd env.
CYRILLIC_BYPASS = os.environ.get("KIRO_GUARD_CYRILLIC_BYPASS", "0") not in (
    "0",
    "false",
    "False",
)
_LATIN_C = "c"
_CYRILLIC_C = "\u0441"  # Cyrillic small es — visually identical to latin c

# Proactive output cap: reduce max_tokens before sending upstream to keep
# responses under kiro's ~9KB buffer limit. 0 = no cap.
# Adaptive: requests with Write/Edit tools get WRITE_CAP (higher); others get base CAP.
MAX_TOKENS_CAP = int(os.environ.get("KIRO_GUARD_MAX_TOKENS_CAP", "4096"))
MAX_TOKENS_WRITE_CAP = int(os.environ.get("KIRO_GUARD_MAX_TOKENS_WRITE_CAP", "8192"))
_WRITE_TOOL_NAMES = frozenset({"Write", "Edit", "NotebookEdit", "write_file", "edit_file"})
# HTTP response compression: gzip bodies > GZIP_MIN_BYTES when client accepts.
GZIP_MIN_BYTES = int(os.environ.get("KIRO_GUARD_GZIP_MIN", "1024"))
MAX_RESPONSE_BYTES = int(os.environ.get("KIRO_GUARD_MAX_RESPONSE_BYTES", str(10 * 1024 * 1024)))
JOURNAL_MAX_BYTES = int(os.environ.get("KIRO_GUARD_JOURNAL_MAX_BYTES", str(5 * 1024 * 1024)))
JOURNAL_KEEP = int(os.environ.get("KIRO_GUARD_JOURNAL_KEEP", "3"))
METRICS_SNAPSHOT_PATH = os.environ.get(
    "KIRO_GUARD_METRICS_SNAPSHOT",
    os.path.join(os.path.dirname(JOURNAL_PATH), f"kiro-guard-metrics-{LISTEN_PORT}.json"),
)
# Clamp >=10s: 0 would busy-loop the snapshot thread.
METRICS_SNAPSHOT_INTERVAL = max(10, int(os.environ.get("KIRO_GUARD_METRICS_SNAPSHOT_INTERVAL", "300")))
# Concurrency limiter: max simultaneous upstream requests. 0 = unlimited.
UPSTREAM_CONCURRENCY = int(os.environ.get("KIRO_GUARD_UPSTREAM_CONCURRENCY", "3"))
# Streaming passthrough: when enabled, forward stream:true directly to upstream
# instead of stream:false + SSE synthesis. Use when kiro fixes truncation.
STREAM_PASSTHROUGH = os.environ.get("KIRO_GUARD_STREAM_PASSTHROUGH", "0") not in (
    "0", "false", "False",
)
# Tee streaming: forward upstream SSE to the client as it arrives while
# reassembling the message for classify; holds back terminal events until the
# verdict, and continues the stream on text truncation (low TTFT + guard).
TEE = os.environ.get("KIRO_GUARD_TEE", "1") not in ("0", "false", "False")
# Active request cap: reject new /v1/messages with 503 when full. 0 = unlimited.
MAX_ACTIVE_REQUESTS = int(os.environ.get("KIRO_GUARD_MAX_ACTIVE_REQUESTS", "16"))
# TG alert: notify on consecutive hard failures.
TG_BOT_TOKEN = os.environ.get("KIRO_GUARD_TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("KIRO_GUARD_TG_CHAT_ID", "")
TG_ALERT_THRESHOLD = int(os.environ.get("KIRO_GUARD_TG_ALERT_THRESHOLD", "5"))
# Codex CLI header spoof: inject Originator/User-Agent/Version to pass
# AgentRouter WAF. Enable only on AR guard instances.
CODEX_SPOOF = os.environ.get("KIRO_GUARD_CODEX_SPOOF", "0") not in (
    "0", "false", "False",
)
_CODEX_HEADERS = {
    "Originator": "codex_cli_rs",
    "User-Agent": "codex_cli_rs/0.101.0 (Mac OS 26.0.1; arm64) Apple_Terminal/464",
    "Version": "0.101.0",
}
# AnyRouter 1M context: inject/append anthropic-beta header so upstream
# grants 1M context window. Enable on AnyRouter guard instances only.
ANYROUTER_1M = os.environ.get("KIRO_GUARD_ANYROUTER_1M", "0") not in (
    "0", "false", "False",
)
_ANYROUTER_1M_BETA = "context-1m-2025-08-07"

# Truncation-aware retry (kirocc spirit): on text truncation, inject the
# truncated response as assistant context + continuation prompt so the model
# picks up where it left off instead of regenerating from scratch.
TRUNC_CONTEXT = os.environ.get("KIRO_GUARD_TRUNC_CONTEXT", "1") not in (
    "0", "false", "False",
)
TRUNC_CONTINUE_PROMPT = (
    "Your previous response was truncated by the infrastructure mid-output. "
    "Continue EXACTLY from where you were cut off. "
    "Do NOT repeat any content you already produced — "
    "pick up mid-sentence/mid-block if needed."
)
TRUNC_CONTEXT_WINDOW = int(os.environ.get("KIRO_GUARD_TRUNC_CONTEXT_WINDOW", "6"))
_TEXT_TRUNC_REASONS = frozenset({
    "short_completion", "unclosed_fence", "trailing_open",
    "tool_intent_no_call", "empty_content",
})

PASS_HEADERS = [
    "x-api-key",
    "authorization",
    "anthropic-version",
    "anthropic-beta",
    "content-type",
    "user-agent",
]

_journal_lock = threading.Lock()
_soft_counts: Dict[str, int] = {}
_hard_counts: Dict[str, int] = {}
_ok_count = 0
_LATENCY_WINDOW = 200
_latencies = collections.deque(maxlen=_LATENCY_WINDOW)
_upstream_sem = threading.Semaphore(UPSTREAM_CONCURRENCY) if UPSTREAM_CONCURRENCY > 0 else None
_active_req_sem = (
    threading.BoundedSemaphore(MAX_ACTIVE_REQUESTS) if MAX_ACTIVE_REQUESTS > 0 else None
)
_active_req_count = 0
_active_req_lock = threading.Lock()
_consecutive_hard = 0
_last_tg_alert_ts = 0.0


def _active_req_acquire() -> bool:
    """Non-blocking active-request slot acquire (P2-11)."""
    global _active_req_count
    if _active_req_sem is None:
        return True
    if not _active_req_sem.acquire(blocking=False):
        return False
    with _active_req_lock:
        _active_req_count += 1
    return True


def _active_req_release() -> None:
    global _active_req_count
    if _active_req_sem is None:
        return
    with _active_req_lock:
        _active_req_count -= 1
    _active_req_sem.release()


def _log(msg: str) -> None:
    sys.stderr.write("[kiro-guard] " + msg + "\n")


def _redact_proxy(p: str) -> str:
    """Strip user:pass@ from proxy URL before echoing in /metrics."""
    return re.sub(r"(://)[^/@]+@", r"\1", p)


def _bump(counter: Dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def _save_metrics_snapshot() -> None:
    """Persist in-memory metrics to disk. Called periodically by background thread."""
    try:
        snap = {
            "ts": int(time.time()),
            "port": LISTEN_PORT,
            "ok": _ok_count,
            "soft": dict(_soft_counts),
            "hard": dict(_hard_counts),
            "latencies": list(_latencies),
        }
        tmp = METRICS_SNAPSHOT_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
        os.replace(tmp, METRICS_SNAPSHOT_PATH)
    except Exception as e:
        _log(f"metrics_snapshot_error {type(e).__name__}:{e}")


def _load_metrics_snapshot() -> None:
    """Restore metrics from last snapshot on startup."""
    global _ok_count
    try:
        if not os.path.exists(METRICS_SNAPSHOT_PATH):
            return
        with open(METRICS_SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            snap = json.load(f)
        if snap.get("port") != LISTEN_PORT:
            return
        _ok_count = snap.get("ok", 0)
        _soft_counts.update(snap.get("soft", {}))
        _hard_counts.update(snap.get("hard", {}))
        saved_lat = snap.get("latencies", [])
        if saved_lat:
            _latencies.extend(saved_lat[-_LATENCY_WINDOW:])
        _log(f"metrics_restored ok={_ok_count} soft={sum(_soft_counts.values())} "
             f"hard={sum(_hard_counts.values())} latencies={len(_latencies)}")
    except Exception as e:
        _log(f"metrics_restore_error {type(e).__name__}:{e}")


def _metrics_snapshot_loop() -> None:
    """Background thread: save metrics snapshot every METRICS_SNAPSHOT_INTERVAL seconds."""
    while True:
        time.sleep(METRICS_SNAPSHOT_INTERVAL)
        _save_metrics_snapshot()


def _tg_alert(message: str) -> None:
    """Cooldown-gated TG alert. Cooldown decided on caller thread; the network
    send itself is fire-and-forget on a daemon thread so request paths never block."""
    global _last_tg_alert_ts
    now = time.time()
    if now - _last_tg_alert_ts < 60:
        return
    _last_tg_alert_ts = now
    threading.Thread(target=_tg_send, args=(message,), daemon=True).start()


def _tg_send(message: str) -> None:
    """Send a TG bot alert via tg_notify (reuses VPS proxy + token). Never raises."""
    try:
        from tg_notify import send_tg
        send_tg(message, markdown=False)
        _log(f"tg_alert_sent len={len(message)}")
    except ImportError:
        if not TG_BOT_TOKEN or not TG_CHAT_ID:
            return
        try:
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            data = json.dumps({"chat_id": TG_CHAT_ID, "text": message}).encode()
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
            _log(f"tg_alert_sent len={len(message)} (direct)")
        except Exception as e:
            _log(f"tg_alert_error {type(e).__name__}:{e}")
    except Exception as e:
        _log(f"tg_alert_error {type(e).__name__}:{e}")


def _check_hard_alert(reason: str) -> None:
    """Increment consecutive hard counter; alert if threshold reached."""
    global _consecutive_hard
    _consecutive_hard += 1
    if _consecutive_hard >= TG_ALERT_THRESHOLD:
        _tg_alert(
            f"[kiro-guard:{LISTEN_PORT}] {_consecutive_hard} consecutive hard failures\n"
            f"last: {reason}\nupstream: {UPSTREAM}"
        )
        _consecutive_hard = 0


def _reset_hard_counter() -> None:
    """Reset consecutive hard counter on success."""
    global _consecutive_hard
    _consecutive_hard = 0


def _rotate_journal() -> None:
    """Rotate journal file when it exceeds JOURNAL_MAX_BYTES. Caller holds _journal_lock."""
    try:
        if not os.path.exists(JOURNAL_PATH):
            return
        sz = os.path.getsize(JOURNAL_PATH)
        if sz < JOURNAL_MAX_BYTES:
            return
        for i in range(JOURNAL_KEEP - 1, 0, -1):
            src = f"{JOURNAL_PATH}.{i}"
            dst = f"{JOURNAL_PATH}.{i + 1}"
            if os.path.exists(src):
                if i + 1 >= JOURNAL_KEEP:
                    os.remove(src)
                else:
                    os.replace(src, dst)
        os.replace(JOURNAL_PATH, f"{JOURNAL_PATH}.1")
        _log(f"journal_rotated size={sz}")
    except Exception as e:
        _log(f"journal_rotate_error {type(e).__name__}:{e}")


def journal_event(kind: str, reason: str, extra: Optional[dict] = None,
                   req_id: str = "") -> None:
    """Append classify events for ops (JSONL). Auto-rotates on size threshold."""
    global _ok_count
    try:
        if kind == "ok":
            _ok_count += 1
        if kind == "soft":
            _bump(_soft_counts, reason.split(":", 1)[0])
        elif kind == "hard":
            _bump(_hard_counts, reason.split(":", 1)[0])
        rec = {
            "ts": int(time.time()),
            "port": LISTEN_PORT,
            "kind": kind,
            "reason": reason,
            "upstream": UPSTREAM,
        }
        if req_id:
            rec["req_id"] = req_id
        if extra:
            rec.update(extra)
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with _journal_lock:
            _rotate_journal()
            with open(JOURNAL_PATH, "a", encoding="utf-8") as f:
                f.write(line)
        _log(f"classify_event kind={kind} reason={reason}"
             + (f" req_id={req_id}" if req_id else ""))
    except Exception as e:
        _log(f"journal_error {type(e).__name__}:{e}")


def _opener() -> urllib.request.OpenerDirector:
    """Build opener; optional PROXY for upstream (never used for listen bind)."""
    if PROXY:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
        )
    # Avoid inheriting ambient env proxies accidentally when PROXY unset.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


_UPSTREAM_OPENER = _opener()


def _read_limited(fp, limit: int = MAX_RESPONSE_BYTES) -> bytes:
    data = fp.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"response body exceeds {limit} bytes")
    return data


def _record_latency(elapsed_ms: float) -> None:
    _latencies.append(elapsed_ms)  # deque(maxlen) drops oldest automatically


def _fetch_upstream(req: urllib.request.Request, timeout: float = TIMEOUT) -> Tuple[int, bytes]:
    if _upstream_sem is not None:
        _upstream_sem.acquire()
    t0 = time.monotonic()
    try:
        with _UPSTREAM_OPENER.open(req, timeout=timeout) as resp:
            data = _read_limited(resp)
            _record_latency((time.monotonic() - t0) * 1000)
            return resp.status, data
    except urllib.error.HTTPError as e:
        _record_latency((time.monotonic() - t0) * 1000)
        try:
            data = _read_limited(e)
        except ValueError:
            data = b""
        return e.code, data
    except Exception as e:
        _record_latency((time.monotonic() - t0) * 1000)
        return 502, _api_error(502, "Upstream connection failed. Please retry.")
    finally:
        if _upstream_sem is not None:
            _upstream_sem.release()


def _open_upstream(req: urllib.request.Request, timeout: float = TIMEOUT):
    """Open upstream without reading the body (tee streaming).

    Caller owns the response: must close it and release _upstream_sem.
    On open failure the semaphore is released before re-raising.
    """
    if _upstream_sem is not None:
        _upstream_sem.acquire()
    try:
        return _UPSTREAM_OPENER.open(req, timeout=timeout)
    except Exception:
        if _upstream_sem is not None:
            _upstream_sem.release()
        raise


def detect_content_blocked(status: int, body: bytes) -> Optional[str]:
    """Return a short reason if upstream rejected the prompt via edge filter.

    AgentRouter: ContentBlockedError / sensitive_words_detected / destructive
    keyword 405. Switching model does not help (filter is pre-dispatch).
    """
    if not CONTENT_BLOCK_FAILOVER:
        return None
    text = body.decode("utf-8", errors="replace") if body else ""
    low = text.lower()
    for marker in _CONTENT_BLOCK_MARKERS:
        if marker.lower() in low or marker in text:
            return f"marker:{marker}"
    # AR historically returns 405 for some destructive-keyword prompts on
    # POST /v1/messages (not a real Method Not Allowed).
    if status == 405:
        return "http_405_keyword_suspect"
    # 400 with typed content policy, even if message wording drifts.
    if status == 400 and (
        "content" in low
        and (
            "block" in low
            or "policy" in low
            or "filter" in low
            or "violat" in low
            or "moderat" in low
        )
    ):
        return "http_400_content_policy"
    return None


def content_blocked_502(reason: str, upstream_status: int, body: bytes) -> bytes:
    return _api_error(502, "Content policy violation. The provider rejected this request.")


def maybe_failover_content_block(
    status: int, body: bytes, phase: str
) -> Optional[Tuple[int, bytes, str, str, Optional[dict]]]:
    """If content-blocked, return immediate 502 tuple (no soft retry)."""
    # Block markers only apply to error responses; a 2xx body may legitimately
    # contain marker words (e.g. the model discussing content policy).
    if 200 <= status < 300:
        return None
    why = detect_content_blocked(status, body)
    if not why:
        return None
    reason = f"content_blocked:{why}"
    journal_event("hard", reason, {"phase": phase, "upstream_http": status})
    _log(f"content_blocked phase={phase} upstream_http={status} why={why}")
    return (
        502,
        content_blocked_502(why, status, body),
        "hard",
        reason,
        None,
    )


def sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


def _api_error(status: int, message: str, error_type: str = "") -> bytes:
    """Build a standard Anthropic-shaped error response.

    Maps guard internals to standard types so Claude Code does not
    misinterpret proxy errors as tool failures.
    """
    if not error_type:
        if status == 529 or status == 503:
            error_type = "overloaded_error"
        elif status == 502:
            error_type = "overloaded_error"
        elif status == 408 or "timeout" in message.lower():
            error_type = "overloaded_error"
        elif status == 429:
            error_type = "rate_limit_error"
        else:
            error_type = "api_error"
    return json.dumps(
        {"type": "error", "error": {"type": error_type, "message": message}},
        ensure_ascii=False,
    ).encode()


SYNTH_CHUNK_SIZE = int(os.environ.get("KIRO_GUARD_SYNTH_CHUNK", "80"))
SYNTH_CHUNK_DELAY = float(os.environ.get("KIRO_GUARD_SYNTH_DELAY", "0.012"))


def synth_stream_progressive(msg: dict, wfile) -> None:
    """Write SSE events progressively with small delays for natural feel."""
    def _w(data: bytes):
        wfile.write(data)
        wfile.flush()

    stub = {k: v for k, v in msg.items() if k != "content"}
    stub["content"] = []
    _w(sse("message_start", {"type": "message_start", "message": stub}))
    for idx, block in enumerate(msg.get("content") or []):
        btype = block.get("type", "text")
        if btype == "text":
            start_block = {"type": "text", "text": ""}
        elif btype == "thinking":
            start_block = {"type": "thinking", "thinking": ""}
        elif btype == "tool_use":
            start_block = {"type": "tool_use", "id": block.get("id", ""), "name": block.get("name", ""), "input": {}}
        else:
            start_block = dict(block)
        _w(sse("content_block_start", {
            "type": "content_block_start", "index": idx,
            "content_block": start_block,
        }))
        if btype == "text":
            text = block.get("text", "")
            pos = 0
            while pos < len(text):
                chunk = text[pos:pos + SYNTH_CHUNK_SIZE]
                _w(sse("content_block_delta", {
                    "type": "content_block_delta", "index": idx,
                    "delta": {"type": "text_delta", "text": chunk},
                }))
                pos += SYNTH_CHUNK_SIZE
                if pos < len(text) and SYNTH_CHUNK_DELAY > 0:
                    time.sleep(SYNTH_CHUNK_DELAY)
        elif btype == "thinking":
            text = block.get("thinking", "")
            pos = 0
            while pos < len(text):
                chunk = text[pos:pos + SYNTH_CHUNK_SIZE * 2]
                _w(sse("content_block_delta", {
                    "type": "content_block_delta", "index": idx,
                    "delta": {"type": "thinking_delta", "thinking": chunk},
                }))
                pos += SYNTH_CHUNK_SIZE * 2
                if pos < len(text) and SYNTH_CHUNK_DELAY > 0:
                    time.sleep(SYNTH_CHUNK_DELAY / 2)
        elif btype == "tool_use":
            _w(sse("content_block_delta", {
                "type": "content_block_delta", "index": idx,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": json.dumps(
                        block.get("input", {}), ensure_ascii=False
                    ),
                },
            }))
        _w(sse("content_block_stop", {"type": "content_block_stop", "index": idx}))
    usage = msg.get("usage") or {}
    _w(sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": msg.get("stop_reason"),
                "stop_sequence": msg.get("stop_sequence"),
            },
            "usage": {"output_tokens": usage.get("output_tokens", 0)},
        },
    ))
    _w(sse("message_stop", {"type": "message_stop"}))


class _SSEParser:
    """Incremental SSE parser: splits event:/data: lines across chunks.

    feed() yields (event, data_str, raw_str) per complete event; raw_str is
    the exact source text of the event (tee forwards it byte-identically).
    """

    def __init__(self):
        self._buf = ""
        self._event = "message"
        self._data: list = []
        self._raw = ""

    def feed(self, chunk: bytes) -> list:
        self._buf += chunk.decode("utf-8", errors="replace")
        events = []
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            ev = self._line(line + "\n")
            if ev is not None:
                events.append(ev)
        return events

    def _line(self, raw_line: str):
        self._raw += raw_line
        line = raw_line.rstrip("\r\n")
        if line == "":
            if self._data:
                ev = (self._event, "\n".join(self._data), self._raw)
                self._event = "message"
                self._data = []
                self._raw = ""
                return ev
            self._raw = ""
            return None
        if line.startswith(":"):
            self._raw = ""
            return None
        if line.startswith("event:"):
            self._event = line[6:].strip()
        elif line.startswith("data:"):
            d = line[5:]
            if d.startswith(" "):
                d = d[1:]
            self._data.append(d)
        return None

    def flush_end(self):
        """Emit any pending event at upstream EOF (no trailing blank line)."""
        ev = None
        if self._buf:
            ev = self._line(self._buf)
            self._buf = ""
        if ev is None and self._data:
            ev = (self._event, "\n".join(self._data), self._raw)
            self._event = "message"
            self._data = []
            self._raw = ""
        if ev is not None and not ev[2].endswith("\n\n"):
            ev = (ev[0], ev[1], ev[2].rstrip("\r\n") + "\n\n")
        return ev


class _MessageAssembler:
    """Rebuild an anthropic message dict from a streamed SSE event flow."""

    def __init__(self):
        self.msg: Optional[dict] = None
        self._blocks: Dict[int, dict] = {}
        self.stop_reason = None
        self.stop_sequence = None
        self.usage: dict = {}

    def handle(self, event: str, data: dict) -> None:
        if not isinstance(data, dict):
            return
        etype = data.get("type") or event
        if etype == "message_start":
            m = data.get("message") or {}
            self.msg = copy.deepcopy(m)
            self.msg.setdefault("content", [])
            u = m.get("usage")
            if isinstance(u, dict):
                self.usage.update(u)
        elif etype == "content_block_start":
            idx = data.get("index", 0)
            self._blocks[idx] = copy.deepcopy(data.get("content_block") or {})
        elif etype == "content_block_delta":
            block = self._blocks.get(data.get("index", 0))
            if block is None:
                return
            delta = data.get("delta") or {}
            dt = delta.get("type")
            if dt == "text_delta":
                block["text"] = (block.get("text") or "") + (delta.get("text") or "")
            elif dt == "thinking_delta":
                block["thinking"] = (block.get("thinking") or "") + (delta.get("thinking") or "")
            elif dt == "signature_delta":
                block["signature"] = (block.get("signature") or "") + (delta.get("signature") or "")
            elif dt == "input_json_delta":
                block["_partial_json"] = (block.get("_partial_json") or "") + (
                    delta.get("partial_json") or ""
                )
        elif etype == "content_block_stop":
            block = self._blocks.get(data.get("index", 0))
            if block is not None and "_partial_json" in block:
                raw = block.pop("_partial_json")
                try:
                    block["input"] = json.loads(raw) if raw else {}
                except Exception:
                    block["input"] = {}
        elif etype == "message_delta":
            delta = data.get("delta") or {}
            if delta.get("stop_reason") is not None:
                self.stop_reason = delta.get("stop_reason")
            if "stop_sequence" in delta:
                self.stop_sequence = delta.get("stop_sequence")
            u = data.get("usage")
            if isinstance(u, dict):
                self.usage.update(u)

    def message(self) -> Optional[dict]:
        if self.msg is None:
            return None
        out = copy.deepcopy(self.msg)
        out["content"] = [self._blocks[i] for i in sorted(self._blocks)]
        out["stop_reason"] = self.stop_reason
        out["stop_sequence"] = self.stop_sequence
        out["usage"] = dict(self.usage)
        return out


class _IndexRemap:
    """Map upstream block indexes to dense client indexes (continuation round).

    Skipped blocks (thinking, buffered first text) still get assigned client
    indexes in emission order so the client sees a contiguous sequence.
    """

    def __init__(self, base: int):
        self._base = base
        self._map: Dict[int, int] = {}

    def start(self, upstream_idx: int) -> int:
        client_idx = self._base + len(self._map)
        self._map[upstream_idx] = client_idx
        return client_idx

    def get(self, upstream_idx: int) -> int:
        return self._map.get(upstream_idx, upstream_idx + self._base)


def _content_nonempty(content: Any) -> bool:
    if not isinstance(content, list) or not content:
        return False
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text" and (block.get("text") or "").strip():
            return True
        if btype == "tool_use" and block.get("name"):
            return True
        if btype == "thinking" and (block.get("thinking") or "").strip():
            return True
    return False


def _assistant_text(content: list) -> str:
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text") or "")
    return "".join(parts)


def _has_tool_use(content: list) -> bool:
    return any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)


def classify_tool_blocks(content: list) -> Optional[Tuple[str, str]]:
    """Detect truncated/empty tool_use (kiro-gateway #56 Write: {}).

    Returns (kind, reason) or None if tools look fine.
    """
    tool_blocks = [
        b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    if not tool_blocks:
        return None
    for block in tool_blocks:
        name = (block.get("name") or "").strip()
        if not name:
            return "hard", "tool_missing_name"
        inp = block.get("input")
        if not isinstance(inp, dict):
            return "hard", "tool_incomplete"
        # Empty object is the classic mid-stream truncate fingerprint.
        if len(inp) == 0:
            return "soft", "empty_tool_input"
    return None


_INTENT_RE = re.compile(
    r"(我将|我来|让我|接下来我会|接下来我|我会先|先去|准备|"
    r"I(?:'ll| will)|Let me|I am going to|I'm going to|Next[, ]I)",
    re.I,
)


def classify_text_incomplete(content: list, request_payload: dict):
    """Community-portable heuristics for fake end_turn with non-trivial text.

    Ported spirit from Kiro-Go #141/#142: do not trust end_turn when the
    visible reply looks truncated / intention-only. False positives → soft
    retry then 502 (channel switch) — acceptable vs silent half-answers.
    """
    if not TEXT_HEUR:
        return None
    blob = _assistant_text(content).rstrip()
    if not blob:
        return None

    if blob.count("```") % 2 == 1:
        return "unclosed_fence"

    if blob.endswith(("...", "…", "，", "、", "：", ":", "；", ";", "（", "(", "【", "[", "「", "『")):
        return "trailing_open"

    tools = request_payload.get("tools")
    if tools and not _has_tool_use(content) and len(blob) <= INTENT_MAX_CHARS:
        tail = blob[-240:] if len(blob) > 240 else blob
        if _INTENT_RE.search(tail):
            return "tool_intent_no_call"

    return None


def classify_response(
    body: bytes, request_payload: dict
) -> Tuple[str, str, Optional[dict]]:
    """Return (kind, reason, msg) where kind in ok|hard|soft."""
    try:
        msg = json.loads(body)
    except Exception as e:
        return "hard", f"invalid_json:{e}", None
    if not isinstance(msg, dict):
        return "hard", "not_object", None
    if "content" not in msg:
        return "hard", "missing_content", None
    if msg.get("stop_reason") is None:
        return "hard", "missing_stop_reason", None

    content = msg.get("content")
    if not isinstance(content, list):
        return "hard", "content_not_list", None

    for block in content:
        if not isinstance(block, dict):
            return "hard", "block_not_object", None

    tool_verdict = classify_tool_blocks(content)
    if tool_verdict:
        return tool_verdict[0], tool_verdict[1], msg

    # stop_reason claims tool_use but no tool block → soft (truncated before emit)
    if msg.get("stop_reason") == "tool_use" and not _has_tool_use(content):
        return "soft", "tool_use_no_block", msg

    if not _content_nonempty(content) and msg.get("stop_reason") == "end_turn":
        return "soft", "empty_content", msg

    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return "soft", "missing_usage", msg
    if usage.get("output_tokens") is None and _content_nonempty(content):
        return "soft", "missing_output_tokens", msg

    # Short-completion gate (conservative): large input + tiny output + end_turn
    # Skip tiny request budgets (probes / classifiers).
    req_max = int(request_payload.get("max_tokens") or 0)
    out_tok = usage.get("output_tokens")
    in_tok = usage.get("input_tokens")
    try:
        out_tok_i = int(out_tok) if out_tok is not None else None
        in_tok_i = int(in_tok) if in_tok is not None else None
    except (TypeError, ValueError):
        return "soft", "usage_not_int", msg

    if (
        req_max >= SHORT_MAX_TOKENS_GATE
        and in_tok_i is not None
        and in_tok_i >= SHORT_IN_TOKENS
        and out_tok_i is not None
        and 0 <= out_tok_i <= SHORT_OUT_TOKENS
        and msg.get("stop_reason") == "end_turn"
    ):
        return "soft", "short_completion", msg

    if msg.get("stop_reason") == "end_turn":
        why = classify_text_incomplete(content, request_payload)
        if why:
            return "soft", why, msg

    return "ok", "ok", msg


def build_continuation_payload(
    original_payload: dict, truncated_msg: dict
) -> dict:
    """Build retry payload with truncated response as context (kirocc spirit).

    Keeps system message + last TRUNC_CONTEXT_WINDOW messages + truncated
    assistant turn + continuation prompt. This avoids re-sending the entire
    conversation history, roughly halving input tokens for long sessions.
    """
    out = copy.deepcopy(original_payload)
    trunc_content = truncated_msg.get("content", [])
    if not trunc_content:
        return out
    messages = list(out.get("messages") or [])
    if TRUNC_CONTEXT_WINDOW > 0 and len(messages) > TRUNC_CONTEXT_WINDOW:
        head = []
        if messages and messages[0].get("role") == "user":
            first_content = messages[0].get("content")
            if isinstance(first_content, list) and any(
                isinstance(b, dict) and b.get("type") == "text"
                and b.get("cache_control")
                for b in first_content
            ):
                head = [messages[0]]
            elif isinstance(first_content, str) and len(first_content) > 2000:
                head = [messages[0]]
        trimmed = messages[-TRUNC_CONTEXT_WINDOW:]
        if trimmed and trimmed[0].get("role") == "assistant":
            trimmed = trimmed[1:]
        if head and trimmed and head[-1].get("role") == trimmed[0].get("role"):
            trimmed = trimmed[1:]
        messages = head + trimmed
        _log(f"continuation_trimmed kept={len(messages)} window={TRUNC_CONTEXT_WINDOW}")
    messages.append({"role": "assistant", "content": trunc_content})
    messages.append({
        "role": "user",
        "content": [{"type": "text", "text": TRUNC_CONTINUE_PROMPT}],
    })
    out["messages"] = messages
    return out


def _dedup_overlap(tail: str, head: str, min_match: int = 20) -> str:
    """Remove overlapping text between truncated tail and continuation head.

    The model may repeat the last few lines from the truncated response when
    continuing. Find the longest suffix of `tail` that matches a prefix of
    `head` (>= min_match chars) and strip it from `head`.
    """
    if not tail or not head:
        return head
    max_check = min(len(tail), len(head), 500)
    best = 0
    for length in range(min_match, max_check + 1):
        if tail.endswith(head[:length]):
            best = length
    if best >= min_match:
        _log(f"dedup_overlap stripped {best} chars")
        return head[best:]
    return head


def merge_responses(first_msg: dict, cont_msg: dict) -> dict:
    """Merge truncated first response with its continuation (with dedup)."""
    merged = copy.deepcopy(first_msg)
    first_content = list(merged.get("content") or [])
    cont_content = list(cont_msg.get("content") or [])

    last_text_idx = None
    for i in range(len(first_content) - 1, -1, -1):
        if (isinstance(first_content[i], dict)
                and first_content[i].get("type") == "text"):
            last_text_idx = i
            break

    first_cont_text = None
    rest_cont = []
    for block in cont_content:
        if not isinstance(block, dict):
            rest_cont.append(block)
            continue
        if block.get("type") == "thinking":
            continue
        if block.get("type") == "text" and first_cont_text is None:
            first_cont_text = block
        else:
            rest_cont.append(block)

    if last_text_idx is not None and first_cont_text is not None:
        tail = first_content[last_text_idx].get("text", "")
        head = first_cont_text.get("text", "")
        head = _dedup_overlap(tail, head)
        first_content[last_text_idx]["text"] = tail + head
    elif first_cont_text is not None:
        first_content.append(first_cont_text)

    first_content.extend(rest_cont)
    merged["content"] = first_content
    merged["stop_reason"] = cont_msg.get("stop_reason", "end_turn")

    u1 = merged.get("usage") or {}
    u2 = cont_msg.get("usage") or {}
    merged["usage"] = {
        "input_tokens": u2.get("input_tokens") or u1.get("input_tokens") or 0,
        "output_tokens": (u1.get("output_tokens") or 0)
        + (u2.get("output_tokens") or 0),
    }
    for k in ("cache_read_input_tokens", "cache_creation_input_tokens"):
        if k in u2:
            merged["usage"][k] = u2[k]
        elif k in u1:
            merged["usage"][k] = u1[k]

    return merged


def _request_has_bash(request_payload: dict) -> bool:
    tools = request_payload.get("tools")
    if not isinstance(tools, list) or not tools:
        # Claude Code always sends tools; if absent, still prefer Bash rewrite.
        return True
    for t in tools:
        if isinstance(t, dict) and t.get("name") == "Bash":
            return True
    return False


def apply_soft_limit(
    msg: dict, reason: str, request_payload: dict
) -> dict:
    """Rewrite truncated tool calls so the agent loop continues.

    Port of CLIProxyAPIPlus SOFT_LIMIT_REACHED + earlier Bash-echo trick:
    Claude Code executes Bash, sees the notice in tool_result, then retries
    with smaller chunks — better than 502/end_turn silent stop.
    """
    out = dict(msg)
    content_in = list(out.get("content") or [])
    content_out: list = []
    replaced = 0
    path_hint = ""

    for block in content_in:
        if not isinstance(block, dict):
            content_out.append(block)
            continue
        if block.get("type") != "tool_use":
            content_out.append(block)
            continue
        inp = block.get("input")
        bad = (
            reason in ("empty_tool_input", "tool_incomplete", "tool_missing_name")
            or not isinstance(inp, dict)
            or len(inp) == 0
        )
        if not bad:
            content_out.append(block)
            continue
        if isinstance(inp, dict):
            path_hint = (
                path_hint
                or str(inp.get("path") or inp.get("file_path") or "")
            )
        tid = block.get("id") or f"toolu_soft_{int(time.time())}_{replaced}"
        notice = SOFT_LIMIT_MSG
        if path_hint:
            notice += f" Path hint: {path_hint}."
        if _request_has_bash(request_payload):
            # Escape for single-quoted echo
            safe = notice.replace("'", "'\\''")
            content_out.append(
                {
                    "type": "tool_use",
                    "id": tid,
                    "name": "Bash",
                    "input": {"command": f"echo '[SOFT_LIMIT_REACHED] {safe}'"},
                }
            )
        else:
            content_out.append(
                {
                    "type": "tool_use",
                    "id": tid,
                    "name": block.get("name") or "Write",
                    "input": {
                        "_status": "SOFT_LIMIT_REACHED",
                        "_message": notice,
                    },
                }
            )
        replaced += 1

    if reason == "tool_use_no_block" or replaced == 0:
        tid = f"toolu_soft_{int(time.time())}"
        notice = SOFT_LIMIT_MSG
        if _request_has_bash(request_payload):
            safe = notice.replace("'", "'\\''")
            content_out.append(
                {
                    "type": "tool_use",
                    "id": tid,
                    "name": "Bash",
                    "input": {"command": f"echo '[SOFT_LIMIT_REACHED] {safe}'"},
                }
            )
        else:
            content_out.append(
                {
                    "type": "text",
                    "text": f"[System Notice] {notice}",
                }
            )
            out["stop_reason"] = "end_turn"
            out["content"] = content_out
            return out

    out["content"] = content_out
    out["stop_reason"] = "tool_use"
    return out


def _cyrillic_encode_text(text: str) -> str:
    """Break AR keyword signatures: latin 'c' → Cyrillic 'с' (community)."""
    return text.replace(_LATIN_C, _CYRILLIC_C)


_CYRILLIC_DECODE_RE = re.compile("(?<=[A-Za-z0-9])\\u0441(?=[A-Za-z0-9])")


def _cyrillic_decode_text(text: str) -> str:
    """Restore latin c only inside latin words; real Russian text stays intact."""
    return _CYRILLIC_DECODE_RE.sub(_LATIN_C, text)


def cyrillic_encode_payload(payload: dict) -> dict:
    """Encode system + message text blocks only (not tool schemas / tool_use)."""
    out = copy.deepcopy(payload)
    system = out.get("system")
    if isinstance(system, str):
        out["system"] = _cyrillic_encode_text(system)
    elif isinstance(system, list):
        for part in system:
            if (
                isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
            ):
                part["text"] = _cyrillic_encode_text(part["text"])
    for msg in out.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = _cyrillic_encode_text(content)
        elif isinstance(content, list):
            for part in content:
                if (
                    isinstance(part, dict)
                    and part.get("type") == "text"
                    and isinstance(part.get("text"), str)
                ):
                    part["text"] = _cyrillic_encode_text(part["text"])
    return out


def cyrillic_decode_bytes(body: bytes) -> bytes:
    if not body:
        return body
    try:
        return _cyrillic_decode_text(body.decode("utf-8")).encode("utf-8")
    except Exception:
        return body


def _effective_cap(payload: dict) -> int:
    """Return the adaptive max_tokens cap for this request.

    Priority: thinking budget → Write tools → default cap.
    When extended thinking is active, cap must leave room for visible output
    on top of thinking budget tokens.
    """
    if MAX_TOKENS_CAP <= 0:
        return 0
    base = MAX_TOKENS_CAP
    tools = payload.get("tools")
    if isinstance(tools, list):
        for t in tools:
            if isinstance(t, dict) and t.get("name") in _WRITE_TOOL_NAMES:
                base = MAX_TOKENS_WRITE_CAP
                break
    thinking = payload.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") == "enabled":
        budget = thinking.get("budget_tokens", 0)
        if isinstance(budget, int) and budget > 0:
            return max(base, budget + 2048)
    return base


def build_messages_request(payload: dict, header_map: Dict[str, str], stream: bool = False) -> urllib.request.Request:
    body = dict(payload)
    body["stream"] = stream
    cap = _effective_cap(body)
    if cap > 0:
        orig = body.get("max_tokens")
        if isinstance(orig, int) and orig > cap:
            body["max_tokens"] = cap
            _log(f"max_tokens capped {orig} → {cap}")
    req = urllib.request.Request(
        UPSTREAM.rstrip("/") + "/v1/messages",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
    )
    for h, v in header_map.items():
        req.add_header(h, v)
    req.add_header("Content-Type", "application/json")
    return req


def _usage_extra(msg: Optional[dict]) -> Optional[dict]:
    """Extract usage/cache fields from response for journal tracking."""
    if msg is None:
        return None
    u = msg.get("usage")
    if not isinstance(u, dict):
        return None
    extra: dict = {}
    for k in ("input_tokens", "output_tokens",
              "cache_read_input_tokens", "cache_creation_input_tokens"):
        if k in u:
            extra[k] = u[k]
    return extra or None


def fetch_classified(
    payload: dict, header_map: Dict[str, str], req_id: str = ""
) -> Tuple[int, bytes, str, str, Optional[dict]]:
    """Fetch upstream with soft retry + backoff. Returns status, body, kind, reason, msg."""
    if not req_id:
        req_id = uuid.uuid4().hex[:12]
    _log(f"req_start req_id={req_id}")
    # Overall deadline: the handler only waits TIMEOUT for a result, so the
    # soft-retry loop below must fit within the same budget (+backoff slack).
    t0 = time.monotonic()
    # Original payload kept for classify heuristics (tools, max_tokens).
    upstream_payload = cyrillic_encode_payload(payload) if CYRILLIC_BYPASS else payload
    if CYRILLIC_BYPASS:
        _log("cyrillic_bypass encode=on")
    req = build_messages_request(upstream_payload, header_map)
    status, body = _fetch_upstream(req)
    blocked = maybe_failover_content_block(status, body, "first")
    if blocked is not None:
        return blocked
    if status != 200:
        journal_event("hard", f"upstream_http_{status}", req_id=req_id)
        _check_hard_alert(f"upstream_http_{status}")
        return status, body, "hard", f"upstream_http_{status}", None
    if CYRILLIC_BYPASS:
        body = cyrillic_decode_bytes(body)

    kind, reason, msg = classify_response(body, payload)
    if kind == "ok":
        _reset_hard_counter()
        journal_event("ok", "ok", _usage_extra(msg), req_id=req_id)
        return status, body, kind, reason, msg
    if kind == "hard":
        if (
            SOFT_LIMIT
            and msg is not None
            and reason in SOFT_LIMIT_REASONS
        ):
            recovered = apply_soft_limit(msg, reason, payload)
            journal_event("soft", f"soft_limit:{reason}", {"phase": "hard_to_soft_limit"}, req_id=req_id)
            _log(f"soft_limit from hard reason={reason}")
            return (
                200,
                json.dumps(recovered, ensure_ascii=False).encode(),
                "ok",
                f"soft_limit:{reason}",
                recovered,
            )
        journal_event("hard", reason, req_id=req_id)
        _check_hard_alert(reason)
        _log(f"hard_fail reason={reason}")
        return status, body, kind, reason, msg

    journal_event("soft", reason, {"phase": "first"}, req_id=req_id)
    # soft: retry with backoff (Kiro-Go #143). For text truncations, inject
    # truncated content as context so the model continues (kirocc spirit).
    acc_msg: Optional[dict] = None  # accumulated merges across continuation rounds
    for attempt in range(SOFT_RETRY):
        if SOFT_RETRY_BACKOFF_MS > 0:
            time.sleep(SOFT_RETRY_BACKOFF_MS / 1000.0)
        # Stop once the deadline budget is exhausted; otherwise cap the retry
        # call at the remaining budget so total time stays <= TIMEOUT (+slack).
        remaining = TIMEOUT - (time.monotonic() - t0)
        if remaining <= 0:
            _log(f"soft_retry skipped reason={reason} deadline_exceeded")
            break
        use_continuation = (
            TRUNC_CONTEXT
            and reason in _TEXT_TRUNC_REASONS
            and msg is not None
            and _content_nonempty(msg.get("content", []))
        )
        if use_continuation:
            retry_payload = build_continuation_payload(payload, acc_msg or msg)
            retry_upstream = (
                cyrillic_encode_payload(retry_payload)
                if CYRILLIC_BYPASS else retry_payload
            )
            _log(
                f"soft_retry attempt={attempt + 1} reason={reason} "
                f"mode=continuation backoff_ms={SOFT_RETRY_BACKOFF_MS}"
            )
        else:
            retry_upstream = upstream_payload
            _log(
                f"soft_retry attempt={attempt + 1} reason={reason} "
                f"mode=replay backoff_ms={SOFT_RETRY_BACKOFF_MS}"
            )
        status2, body2 = _fetch_upstream(
            build_messages_request(retry_upstream, header_map), timeout=remaining
        )
        blocked2 = maybe_failover_content_block(status2, body2, f"after_soft_{attempt + 1}")
        if blocked2 is not None:
            return blocked2
        if status2 != 200:
            journal_event("hard", f"upstream_http_{status2}_after_soft", req_id=req_id)
            _check_hard_alert(f"upstream_http_{status2}_after_soft")
            return status2, body2, "hard", f"upstream_http_{status2}_after_soft", None
        if CYRILLIC_BYPASS:
            body2 = cyrillic_decode_bytes(body2)
        kind2, reason2, msg2 = classify_response(body2, payload)
        if kind2 == "ok":
            _reset_hard_counter()
            # Merge onto the msg this round's continuation was built from
            # (with SOFT_RETRY>=2 that is the accumulated msg, not the first).
            if use_continuation and msg is not None:
                merged = merge_responses(acc_msg or msg, msg2)
                _extra_m = {"phase": "retry_ok_merged"}
                _extra_m.update(_usage_extra(merged) or {})
                journal_event(
                    "soft", f"recovered_merged:{reason}",
                    _extra_m, req_id=req_id,
                )
                _log(f"trunc_context merged reason={reason}")
                return (
                    200,
                    json.dumps(merged, ensure_ascii=False).encode(),
                    "ok",
                    f"merged:{reason}",
                    merged,
                )
            _extra_r = {"phase": "retry_ok"}
            _extra_r.update(_usage_extra(msg2) or {})
            journal_event("soft", f"recovered:{reason}", _extra_r, req_id=req_id)
            return status2, body2, kind2, reason2, msg2
        if kind2 == "hard":
            if (
                SOFT_LIMIT
                and msg2 is not None
                and reason2 in SOFT_LIMIT_REASONS
            ):
                recovered = apply_soft_limit(msg2, reason2, payload)
                journal_event(
                    "soft", f"soft_limit:{reason2}", {"phase": "hard_after_soft"}, req_id=req_id,
                )
                return (
                    200,
                    json.dumps(recovered, ensure_ascii=False).encode(),
                    "ok",
                    f"soft_limit:{reason2}",
                    recovered,
                )
            journal_event("hard", reason2, {"phase": "after_soft"}, req_id=req_id)
            _check_hard_alert(reason2)
            _log(f"hard_fail_after_soft reason={reason2}")
            return status2, body2, kind2, reason2, msg2
        journal_event("soft", reason2, {"phase": f"retry_{attempt + 1}"}, req_id=req_id)
        if (
            use_continuation
            and msg2 is not None
            and reason2 in _TEXT_TRUNC_REASONS
        ):
            acc_msg = merge_responses(acc_msg or msg, msg2)
        reason, body, msg = reason2, body2, msg2

    _log(f"soft_exhausted reason={reason}")
    if SOFT_LIMIT and msg is not None and reason in SOFT_LIMIT_REASONS:
        recovered = apply_soft_limit(msg, reason, payload)
        journal_event("soft", f"soft_limit:{reason}", {"phase": "exhausted_soft_limit"}, req_id=req_id)
        _log(f"soft_limit after exhausted reason={reason}")
        return (
            200,
            json.dumps(recovered, ensure_ascii=False).encode(),
            "ok",
            f"soft_limit:{reason}",
            recovered,
        )
    journal_event("soft", f"retry_exhausted:{reason}", {"phase": "exhausted"}, req_id=req_id)
    return status, body, "soft", f"retry_exhausted:{reason}", msg


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[kiro-guard] " + (fmt % args) + "\n")

    def _header_map(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for h in PASS_HEADERS:
            v = self.headers.get(h)
            if v:
                out[h] = v
        if CODEX_SPOOF:
            out.update(_CODEX_HEADERS)
        if ANYROUTER_1M:
            existing = out.get("anthropic-beta", "")
            if _ANYROUTER_1M_BETA not in existing:
                out["anthropic-beta"] = (
                    f"{existing},{_ANYROUTER_1M_BETA}" if existing else _ANYROUTER_1M_BETA
                )
        return out

    def do_POST(self):
        if self.path.startswith("/v1/chat/completions"):
            return self._passthrough()
        if self.path.startswith("/v1/messages/count_tokens"):
            return self._count_tokens()
        if not self.path.startswith("/v1/messages"):
            self.send_error(404)
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.send_error(400, "bad content-length")
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except Exception:
            self.send_error(400, "bad json")
            return

        want_stream = bool(payload.get("stream"))
        header_map = self._header_map()

        if STREAM_PASSTHROUGH and want_stream:
            return self._stream_passthrough(payload, header_map)
        if not _active_req_acquire():
            self._reply(
                503,
                _api_error(503, "The model is currently overloaded. Please retry."),
                "application/json",
            )
            return
        try:
            if TEE and want_stream and not CYRILLIC_BYPASS:
                return self._stream_tee(payload, header_map)
            return self._messages_buffered(payload, header_map, want_stream)
        finally:
            _active_req_release()

    def _messages_buffered(self, payload, header_map, want_stream):
        result_q: queue.Queue = queue.Queue(maxsize=1)

        def worker():
            try:
                result_q.put(fetch_classified(payload, header_map))
            except Exception as e:
                _log(f"worker_error: {e}")
                result_q.put(
                    (
                        502,
                        _api_error(502, "The model is currently overloaded. Please retry."),
                        "hard",
                        "guard_internal",
                        None,
                    )
                )

        threading.Thread(target=worker, daemon=True).start()

        FIRST_WAIT = 4
        PING_EVERY = 10

        if want_stream:
            try:
                status, body, kind, reason, msg = result_q.get(timeout=FIRST_WAIT)
            except queue.Empty:
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    waited = FIRST_WAIT
                    while True:
                        try:
                            status, body, kind, reason, msg = result_q.get(
                                timeout=PING_EVERY
                            )
                            break
                        except queue.Empty:
                            waited += PING_EVERY
                            if waited >= TIMEOUT:
                                self.wfile.write(sse("error", {
                                    "type": "error",
                                    "error": {
                                        "type": "overloaded_error",
                                        "message": "The model is currently overloaded. Please retry.",
                                    },
                                }))
                                self.wfile.flush()
                                return
                            self.wfile.write(sse("ping", {"type": "ping"}))
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return

                try:
                    if status == 200 and kind == "ok" and msg is not None:
                        synth_stream_progressive(msg, self.wfile)
                    elif 400 <= status < 500:
                        try:
                            err_obj = json.loads(body)
                        except Exception:
                            err_obj = {"type": "error", "error": {"type": "api_error", "message": body[:500].decode("utf-8", "replace")}}
                        if status == 401 or status == 403:
                            _err = err_obj.get("error")
                            if isinstance(_err, dict):
                                _err["type"] = (
                                    "authentication_error" if status == 401
                                    else "permission_error"
                                )
                        self.wfile.write(sse("error", err_obj))
                    else:
                        self.wfile.write(sse("error", {
                            "type": "error",
                            "error": {
                                "type": "overloaded_error",
                                "message": "The model is currently overloaded. Please retry.",
                            },
                        }))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return

        else:
            try:
                status, body, kind, reason, msg = result_q.get(timeout=TIMEOUT)
            except queue.Empty:
                self._reply(
                    502,
                    _api_error(502, "The model is currently overloaded. Please retry."),
                    "application/json",
                )
                return

        if status != 200:
            self._reply(status, body, "application/json")
            return

        if kind != "ok" or msg is None:
            self._reply(
                502,
                _api_error(502, "The model is currently overloaded. Please retry."),
                "application/json",
            )
            return

        if want_stream:
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                synth_stream_progressive(msg, self.wfile)
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self._reply(200, json.dumps(msg, ensure_ascii=False).encode(), "application/json")

    def _stream_tee(self, payload, header_map):
        """Tee mode: relay upstream SSE live, classify the reassembled message.

        Terminal events (message_delta with stop_reason / message_stop) are
        held back until classify passes; on text truncation the stream is
        continued from a continuation request with index remapping.
        """
        req_id = uuid.uuid4().hex[:12]
        _log(f"req_start req_id={req_id} mode=tee")
        t0 = time.monotonic()
        deadline = (
            t0 + TIMEOUT * (1 + SOFT_RETRY)
            + (SOFT_RETRY_BACKOFF_MS / 1000.0) * SOFT_RETRY
        )
        headers_sent = False
        acc_msg: Optional[dict] = None
        reason = ""
        round_no = 0
        total_bytes = 0

        def _w(data: bytes) -> None:
            self.wfile.write(data)
            self.wfile.flush()

        def _send_sse_error() -> None:
            _w(sse("error", {
                "type": "error",
                "error": {
                    "type": "overloaded_error",
                    "message": "The model is currently overloaded. Please retry.",
                },
            }))

        try:
            while True:
                if round_no == 0:
                    round_payload = payload
                else:
                    round_payload = build_continuation_payload(payload, acc_msg)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _log(f"tee deadline_exceeded req_id={req_id}")
                    if headers_sent:
                        _send_sse_error()
                    else:
                        self._reply(
                            502,
                            _api_error(502, "The model is currently overloaded. Please retry."),
                            "application/json",
                        )
                    return
                req = build_messages_request(round_payload, header_map, stream=True)
                t_open = time.monotonic()
                try:
                    resp = _open_upstream(req, timeout=min(TIMEOUT, remaining))
                except urllib.error.HTTPError as e:
                    _record_latency((time.monotonic() - t_open) * 1000)
                    try:
                        ebody = _read_limited(e)
                    except ValueError:
                        ebody = b""
                    if not headers_sent:
                        blocked = maybe_failover_content_block(e.code, ebody, "tee_first")
                        if blocked is not None:
                            self._reply(blocked[0], blocked[1], "application/json")
                            return
                        journal_event("hard", f"upstream_http_{e.code}", req_id=req_id)
                        _check_hard_alert(f"upstream_http_{e.code}")
                        self._reply(e.code, ebody, "application/json")
                    else:
                        journal_event(
                            "hard", f"upstream_http_{e.code}_tee_cont", req_id=req_id
                        )
                        _check_hard_alert(f"upstream_http_{e.code}_tee_cont")
                        _send_sse_error()
                    return
                except Exception:
                    _record_latency((time.monotonic() - t_open) * 1000)
                    if headers_sent:
                        _send_sse_error()
                    else:
                        self._reply(
                            502,
                            _api_error(502, "Upstream connection failed. Please retry."),
                            "application/json",
                        )
                    return
                _record_latency((time.monotonic() - t_open) * 1000)
                if not headers_sent:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    headers_sent = True

                parser = _SSEParser()
                asm = _MessageAssembler()
                remap = (
                    _IndexRemap(len(acc_msg.get("content") or [])) if acc_msg else None
                )
                held: list = []
                skip_idx: set = set()
                first_text_idx: Optional[int] = None
                first_text_buf = ""
                first_text_started = False

                def _flush_first_text() -> None:
                    nonlocal first_text_started
                    if first_text_started or first_text_idx is None:
                        return
                    first_text_started = True
                    cidx = remap.get(first_text_idx)
                    _w(sse("content_block_start", {
                        "type": "content_block_start", "index": cidx,
                        "content_block": {"type": "text", "text": ""},
                    }))
                    acc_tail = _assistant_text(acc_msg.get("content") or [])
                    head = _dedup_overlap(acc_tail, first_text_buf)
                    if head:
                        _w(sse("content_block_delta", {
                            "type": "content_block_delta", "index": cidx,
                            "delta": {"type": "text_delta", "text": head},
                        }))

                def _handle_event(ev_name, data_s, raw_ev) -> None:
                    nonlocal first_text_idx, first_text_buf
                    try:
                        data = json.loads(data_s)
                    except Exception:
                        data = {}
                    asm.handle(ev_name, data)
                    etype = data.get("type") if isinstance(data, dict) else None
                    if etype == "message_stop":
                        held.append(raw_ev)
                        return
                    if (
                        etype == "message_delta"
                        and (data.get("delta") or {}).get("stop_reason") is not None
                    ):
                        held.append(raw_ev)
                        return
                    if round_no == 0:
                        _w(raw_ev.encode("utf-8"))
                        return
                    # Continuation round: skip message_start / thinking blocks,
                    # remap indexes, buffer+dedup the first text block.
                    if etype == "message_start":
                        return
                    uidx = data.get("index", 0)
                    if etype == "content_block_start":
                        btype = (data.get("content_block") or {}).get("type")
                        if btype == "thinking":
                            skip_idx.add(uidx)
                            return
                        cidx = remap.start(uidx)
                        if btype == "text" and first_text_idx is None:
                            first_text_idx = uidx
                            return
                        out = dict(data)
                        out["index"] = cidx
                        _w(sse(ev_name, out))
                        return
                    if etype in ("content_block_delta", "content_block_stop"):
                        if uidx in skip_idx:
                            return
                        if first_text_idx is not None and uidx == first_text_idx:
                            if etype == "content_block_delta":
                                d = data.get("delta") or {}
                                if (
                                    d.get("type") == "text_delta"
                                    and not first_text_started
                                ):
                                    first_text_buf += d.get("text") or ""
                                    if len(first_text_buf) < 600:
                                        return
                                    _flush_first_text()
                                    return
                            else:
                                _flush_first_text()
                        out = dict(data)
                        out["index"] = remap.get(uidx)
                        _w(sse(ev_name, out))
                        return
                    _w(sse(ev_name, data))

                try:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        total_bytes += len(chunk)
                        if total_bytes > MAX_RESPONSE_BYTES:
                            raise ValueError("tee response exceeds MAX_RESPONSE_BYTES")
                        for ev_name, data_s, raw_ev in parser.feed(chunk):
                            _handle_event(ev_name, data_s, raw_ev)
                    tail_ev = parser.flush_end()
                    if tail_ev is not None:
                        _handle_event(tail_ev[0], tail_ev[1], tail_ev[2])
                finally:
                    resp.close()
                    if _upstream_sem is not None:
                        _upstream_sem.release()

                round_msg = asm.message()
                if round_msg is None:
                    round_msg = {"content": [], "stop_reason": None}
                kind, rsn, cmsg = classify_response(
                    json.dumps(round_msg, ensure_ascii=False).encode(), payload
                )
                if kind == "ok":
                    final_msg = cmsg if round_no == 0 else merge_responses(acc_msg, cmsg)
                    _reset_hard_counter()
                    if round_no == 0:
                        for raw_ev in held:
                            _w(raw_ev.encode("utf-8"))
                        journal_event("ok", "ok", _usage_extra(final_msg), req_id=req_id)
                    else:
                        u = final_msg.get("usage") or {}
                        _w(sse("message_delta", {
                            "type": "message_delta",
                            "delta": {
                                "stop_reason": final_msg.get("stop_reason"),
                                "stop_sequence": final_msg.get("stop_sequence"),
                            },
                            "usage": {"output_tokens": u.get("output_tokens", 0)},
                        }))
                        _w(sse("message_stop", {"type": "message_stop"}))
                        _extra = {"phase": "tee_retry_ok_merged"}
                        _extra.update(_usage_extra(final_msg) or {})
                        journal_event(
                            "soft", f"recovered_merged:{reason}", _extra, req_id=req_id
                        )
                        _log(f"tee trunc_context merged reason={reason}")
                    return
                if kind == "soft":
                    reason = rsn
                    can_continue = (
                        TRUNC_CONTEXT
                        and rsn in _TEXT_TRUNC_REASONS
                        and cmsg is not None
                        and _content_nonempty(cmsg.get("content", []))
                        and round_no < SOFT_RETRY
                    )
                    if can_continue:
                        journal_event(
                            "soft", rsn, {"phase": f"tee_round_{round_no}"},
                            req_id=req_id,
                        )
                        acc_msg = (
                            cmsg if acc_msg is None else merge_responses(acc_msg, cmsg)
                        )
                        round_no += 1
                        if SOFT_RETRY_BACKOFF_MS > 0:
                            time.sleep(SOFT_RETRY_BACKOFF_MS / 1000.0)
                        continue
                    journal_event(
                        "soft", f"retry_exhausted:{rsn}",
                        {"phase": "tee_exhausted"}, req_id=req_id,
                    )
                    _log(f"tee soft_exhausted reason={rsn}")
                    _send_sse_error()
                    return
                if (
                    kind == "hard"
                    and rsn == "missing_stop_reason"
                    and TRUNC_CONTEXT
                    and round_no < SOFT_RETRY
                    and _content_nonempty(round_msg.get("content", []))
                ):
                    # Upstream closed mid-stream after partial SSE (CF 524 etc.):
                    # treat the partial text as truncation and continue it.
                    reason = rsn
                    _lb = "none"
                    _rc = round_msg.get("content") or []
                    if _rc and isinstance(_rc[-1], dict):
                        _lb = _rc[-1].get("type") or "unknown"
                    journal_event(
                        "soft", f"tee_eof:{rsn}",
                        {"phase": f"tee_round_{round_no}", "last_block": _lb},
                        req_id=req_id,
                    )
                    acc_msg = (
                        round_msg if acc_msg is None else merge_responses(acc_msg, round_msg)
                    )
                    round_no += 1
                    if SOFT_RETRY_BACKOFF_MS > 0:
                        time.sleep(SOFT_RETRY_BACKOFF_MS / 1000.0)
                    continue
                _lb = "none"
                _rc = (round_msg.get("content") or []) if round_msg else []
                if _rc and isinstance(_rc[-1], dict):
                    _lb = _rc[-1].get("type") or "unknown"
                journal_event(
                    "hard", rsn, {"phase": "tee", "last_block": _lb}, req_id=req_id
                )
                _check_hard_alert(rsn)
                _log(f"tee hard_fail reason={rsn}")
                _send_sse_error()
                return
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as e:
            _log(f"stream_tee_error req_id={req_id}: {type(e).__name__}:{e}")
            try:
                if headers_sent:
                    _send_sse_error()
                else:
                    self._reply(
                        502,
                        _api_error(502, "The model is currently overloaded. Please retry."),
                        "application/json",
                    )
            except Exception:
                pass
            return

    def _count_tokens(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        req = urllib.request.Request(
            UPSTREAM.rstrip("/") + self.path.split("?")[0], data=body, method="POST"
        )
        for h, v in self._header_map().items():
            req.add_header(h, v)
        try:
            with _UPSTREAM_OPENER.open(req, timeout=30) as resp:
                self._reply(resp.status, _read_limited(resp), "application/json")
                return
        except Exception:
            pass
        est = max(1, length // 4)
        self._reply(200, json.dumps({"input_tokens": est}).encode(), "application/json")

    def _stream_passthrough(self, payload, header_map):
        if not _active_req_acquire():
            self._reply(
                503,
                _api_error(503, "The model is currently overloaded. Please retry."),
                "application/json",
            )
            return
        try:
            self._stream_passthrough_impl(payload, header_map)
        finally:
            _active_req_release()

    def _stream_passthrough_impl(self, payload, header_map):
        """Forward stream:true directly to upstream and relay SSE chunks as-is."""
        cap = _effective_cap(payload)
        if cap > 0 and (payload.get("max_tokens") or 0) > cap:
            payload["max_tokens"] = cap
        data = json.dumps(payload, ensure_ascii=False).encode()
        req = urllib.request.Request(
            UPSTREAM.rstrip("/") + "/v1/messages", data=data, method="POST"
        )
        for h, v in header_map.items():
            req.add_header(h, v)
        req.add_header("Content-Type", "application/json")
        if _upstream_sem is not None:
            _upstream_sem.acquire()
        try:
            resp = _UPSTREAM_OPENER.open(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            if _upstream_sem is not None:
                _upstream_sem.release()
            try:
                data = _read_limited(e)
            except ValueError:
                data = b""
            self._reply(e.code, data, "application/json")
            return
        except Exception:
            if _upstream_sem is not None:
                _upstream_sem.release()
            self._reply(502, _api_error(502, "Upstream connection failed. Please retry."), "application/json")
            return
        try:
            self.send_response(resp.status)
            ct = resp.headers.get("Content-Type", "text/event-stream")
            self.send_header("Content-Type", ct)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
            _reset_hard_counter()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            _log(f"stream_passthrough_error: {e}")
        finally:
            resp.close()
            if _upstream_sem is not None:
                _upstream_sem.release()

    def _passthrough(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        req = urllib.request.Request(
            UPSTREAM.rstrip("/") + self.path, data=body, method="POST"
        )
        for h, v in self._header_map().items():
            req.add_header(h, v)
        try:
            with _UPSTREAM_OPENER.open(req, timeout=TIMEOUT) as resp:
                data = _read_limited(resp)
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                data = _read_limited(e)
            except ValueError:
                data = b""
        except Exception as e:
            _log(f"passthrough_error: {e}")
            self._reply(502, _api_error(502, "Upstream connection failed. Please retry."), "application/json")
            return
        blocked = maybe_failover_content_block(status, data, "passthrough")
        if blocked is not None:
            st, payload, _, _, _ = blocked
            self._reply(st, payload, "application/json")
            return
        self._reply(status, data, "application/json")

    def _reply(self, status, body, content_type):
        accept_enc = self.headers.get("Accept-Encoding", "")
        use_gzip = (
            "gzip" in accept_enc
            and len(body) > GZIP_MIN_BYTES
            and "event-stream" not in content_type
        )
        if use_gzip:
            buf = io.BytesIO()
            with _gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
                gz.write(body)
            body = buf.getvalue()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._reply(200, b'{"status":"ok"}', "application/json")
        elif self.path == "/metrics":
            lat = {}
            if _latencies:
                s = sorted(_latencies)
                lat = {
                    "count": len(s),
                    "p50_ms": int(s[len(s) // 2]),
                    "p95_ms": int(s[int(len(s) * 0.95)]),
                    "max_ms": int(s[-1]),
                }
            body = json.dumps(
                {
                    "status": "ok",
                    "port": LISTEN_PORT,
                    "upstream": UPSTREAM,
                    "ok": _ok_count,
                    "soft": dict(_soft_counts),
                    "hard": dict(_hard_counts),
                    "upstream_latency": lat,
                    "journal": JOURNAL_PATH,
                    "soft_retry": SOFT_RETRY,
                    "soft_retry_backoff_ms": SOFT_RETRY_BACKOFF_MS,
                    "proxy": _redact_proxy(PROXY) if PROXY else None,
                    "soft_limit": SOFT_LIMIT,
                    "trunc_context": TRUNC_CONTEXT,
                    "max_tokens_cap": MAX_TOKENS_CAP,
                    "max_tokens_write_cap": MAX_TOKENS_WRITE_CAP,
                    "max_response_bytes": MAX_RESPONSE_BYTES,
                    "journal_max_bytes": JOURNAL_MAX_BYTES,
                    "journal_keep": JOURNAL_KEEP,
                    "gzip_min_bytes": GZIP_MIN_BYTES,
                    "upstream_concurrency": UPSTREAM_CONCURRENCY,
                    "stream_passthrough": STREAM_PASSTHROUGH,
                    "tee": TEE,
                    "active_requests": _active_req_count,
                    "max_active_requests": MAX_ACTIVE_REQUESTS,
                    "tg_alert_threshold": TG_ALERT_THRESHOLD,
                    "content_block_failover": CONTENT_BLOCK_FAILOVER,
                    "cyrillic_bypass": CYRILLIC_BYPASS,
                },
                ensure_ascii=False,
            ).encode()
            self._reply(200, body, "application/json")
        else:
            self.send_error(404)


if __name__ == "__main__":
    # Local self-check for classify (no network).
    if os.environ.get("KIRO_GUARD_SELFTEST") == "1":
        soft_body = json.dumps(
            {
                "content": [{"type": "text", "text": "OK"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5000, "output_tokens": 5},
            }
        ).encode()
        kind, reason, _ = classify_response(soft_body, {"max_tokens": 4096})
        assert kind == "soft" and reason == "short_completion", (kind, reason)
        hard_body = b'{"content":[],"stop_reason":null}'
        kind, reason, _ = classify_response(hard_body, {"max_tokens": 64})
        assert kind == "hard", (kind, reason)
        ok_body = json.dumps(
            {
                "content": [{"type": "text", "text": "OK"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }
        ).encode()
        kind, reason, _ = classify_response(ok_body, {"max_tokens": 64})
        assert kind == "ok", (kind, reason)
        fence_body = json.dumps(
            {
                "content": [{"type": "text", "text": "```python\nprint(1)\n"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5000, "output_tokens": 80},
            }
        ).encode()
        kind, reason, _ = classify_response(fence_body, {"max_tokens": 4096})
        assert kind == "soft" and reason == "unclosed_fence", (kind, reason)
        intent_body = json.dumps(
            {
                "content": [{"type": "text", "text": "好的，我将先读取配置文件再继续。"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 3000, "output_tokens": 40},
            }
        ).encode()
        kind, reason, _ = classify_response(
            intent_body, {"max_tokens": 4096, "tools": [{"name": "Read"}]}
        )
        assert kind == "soft" and reason == "tool_intent_no_call", (kind, reason)
        empty_tool = json.dumps(
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Write",
                        "input": {},
                    }
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 4000, "output_tokens": 20},
            }
        ).encode()
        kind, reason, _ = classify_response(empty_tool, {"max_tokens": 4096})
        assert kind == "soft" and reason == "empty_tool_input", (kind, reason)
        no_block = json.dumps(
            {
                "content": [{"type": "text", "text": "x"}],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 100, "output_tokens": 1},
            }
        ).encode()
        kind, reason, _ = classify_response(no_block, {"max_tokens": 4096})
        assert kind == "soft" and reason == "tool_use_no_block", (kind, reason)
        # SOFT_LIMIT rewrite
        empty_msg = json.loads(empty_tool.decode())
        rec = apply_soft_limit(
            empty_msg,
            "empty_tool_input",
            {"tools": [{"name": "Bash"}, {"name": "Write"}]},
        )
        assert rec.get("stop_reason") == "tool_use", rec
        assert rec["content"][0]["name"] == "Bash", rec
        assert "SOFT_LIMIT_REACHED" in rec["content"][0]["input"]["command"], rec
        # content-block failover (AgentRouter keyword / sensitive_words)
        sens = b'{"error":{"message":"sensitive_words_detected"}}'
        assert detect_content_blocked(500, sens) == "marker:sensitive_words_detected"
        assert detect_content_blocked(405, b"") == "http_405_keyword_suspect"
        assert detect_content_blocked(400, b'{"error":"content policy blocked"}')
        assert detect_content_blocked(401, b'{"error":"unauthorized"}') is None
        assert detect_content_blocked(503, b'{"error":"no available channel"}') is None
        fb = maybe_failover_content_block(400, sens, "selftest")
        assert fb is not None and fb[0] == 502 and b"overloaded_error" in fb[1], fb
        # Cyrillic-Bypass roundtrip (community marko1olo)
        enc = cyrillic_encode_payload(
            {
                "system": "create class",
                "messages": [
                    {"role": "user", "content": "check code"},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "function call"},
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "Read",
                                "input": {"path": "src/main.c"},
                            },
                        ],
                    },
                ],
                "tools": [{"name": "Read"}],
            }
        )
        assert enc["system"] == "сreate сlass", enc["system"]
        assert enc["messages"][0]["content"] == "сheсk сode", enc["messages"][0]
        assert enc["messages"][1]["content"][0]["text"] == "funсtion сall"
        # tool_use input / tool schemas must stay untouched
        assert enc["messages"][1]["content"][1]["input"]["path"] == "src/main.c"
        assert enc["tools"][0]["name"] == "Read"
        dec = cyrillic_decode_bytes(
            json.dumps(
                {"content": [{"type": "text", "text": "xсlass Foo"}]},
                ensure_ascii=False,
            ).encode()
        )
        assert b"xclass Foo" in dec, dec
        # Decode is context-aware: cyrillic c keeps real Russian intact (P2-14)
        rus = "всегда сила сокол"
        assert _cyrillic_decode_text(rus) == rus, _cyrillic_decode_text(rus)
        assert _cyrillic_decode_text("funсtion") == "function"
        # word-boundary cyrillic c (no latin neighbour) is left alone
        assert _cyrillic_decode_text("сall") == "сall"
        # Truncation-context: build_continuation_payload
        orig = {
            "model": "claude-opus-5",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": "Write code"}],
            "tools": [{"name": "Write"}],
        }
        trunc = {
            "content": [{"type": "text", "text": "```python\ndef foo():"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 3000, "output_tokens": 12},
        }
        cont_payload = build_continuation_payload(orig, trunc)
        assert len(cont_payload["messages"]) == 3, cont_payload["messages"]
        assert cont_payload["messages"][1]["role"] == "assistant"
        assert cont_payload["messages"][2]["role"] == "user"
        assert "truncated" in cont_payload["messages"][2]["content"][0]["text"]
        # Truncation-context: merge_responses
        cont_resp = {
            "content": [
                {"type": "text", "text": "\n    return 42\n```"},
                {"type": "tool_use", "id": "t1", "name": "Write",
                 "input": {"file_path": "a.py", "content": "x"}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 3500, "output_tokens": 25},
        }
        merged = merge_responses(trunc, cont_resp)
        assert "def foo():" in merged["content"][0]["text"], merged
        assert "return 42" in merged["content"][0]["text"], merged
        assert merged["stop_reason"] == "tool_use", merged
        assert merged["usage"]["output_tokens"] == 37, merged["usage"]
        assert any(b.get("name") == "Write" for b in merged["content"]), merged
        # Dedup overlap: model repeats tail of truncated text (overlap >= 20 chars)
        deduped = _dedup_overlap(
            "The quick brown fox jumps over the lazy dog and sleeps",
            "over the lazy dog and sleeps until morning comes",
        )
        assert deduped == " until morning comes", repr(deduped)
        no_overlap = _dedup_overlap("aaa bbb ccc", "ddd eee fff")
        assert no_overlap == "ddd eee fff", repr(no_overlap)
        short_overlap = _dedup_overlap("x" * 10, "x" * 10)
        assert short_overlap == "x" * 10, repr(short_overlap)  # < min_match
        # Adaptive cap: Write tool → higher cap
        write_req = {"max_tokens": 16384, "tools": [{"name": "Write"}]}
        assert _effective_cap(write_req) == MAX_TOKENS_WRITE_CAP
        plain_req = {"max_tokens": 16384, "tools": [{"name": "Read"}]}
        assert _effective_cap(plain_req) == MAX_TOKENS_CAP
        no_tools = {"max_tokens": 16384}
        assert _effective_cap(no_tools) == MAX_TOKENS_CAP
        # Thinking budget aware cap
        think_req = {
            "max_tokens": 16384,
            "thinking": {"type": "enabled", "budget_tokens": 10000},
        }
        assert _effective_cap(think_req) == 10000 + 2048, _effective_cap(think_req)
        think_write = {
            "max_tokens": 16384,
            "thinking": {"type": "enabled", "budget_tokens": 5000},
            "tools": [{"name": "Write"}],
        }
        cap_tw = _effective_cap(think_write)
        assert cap_tw == max(MAX_TOKENS_WRITE_CAP, 5000 + 2048), cap_tw
        think_disabled = {
            "max_tokens": 16384,
            "thinking": {"type": "disabled"},
        }
        assert _effective_cap(think_disabled) == MAX_TOKENS_CAP
        # Progressive SSE synthesis — no content doubling
        buf = io.BytesIO()
        test_msg = {
            "content": [
                {"type": "thinking", "thinking": "Let me think about this carefully."},
                {"type": "text", "text": "hello world " * 20},
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"path": "/a"}},
            ],
            "stop_reason": "end_turn",
            "usage": {"output_tokens": 10},
        }
        synth_stream_progressive(test_msg, buf)
        sse_out = buf.getvalue().decode()
        assert "message_start" in sse_out
        assert "text_delta" in sse_out
        assert "message_stop" in sse_out
        assert sse_out.count("text_delta") > 1, "should have multiple chunks"
        # Verify thinking block start is empty (no doubling)
        assert '"thinking": ""' in sse_out, "thinking start should be empty"
        assert sse_out.count("Let me think") == 1, "thinking text should appear once only"
        # Verify tool_use block start has empty input
        assert '"input": {}' in sse_out, "tool_use start should have empty input"
        # Tee: SSE parser + assembler roundtrip → classify ok
        parser = _SSEParser()
        asm = _MessageAssembler()
        raw_sse = buf.getvalue()
        for i in range(0, len(raw_sse), 37):  # odd chunk size → split lines
            for ev_name, data_s, raw_ev in parser.feed(raw_sse[i:i + 37]):
                assert raw_ev, "raw event text missing"
                asm.handle(ev_name, json.loads(data_s))
        tail_ev = parser.flush_end()
        assert tail_ev is None, f"unexpected leftover event: {tail_ev}"
        rebuilt = asm.message()
        assert rebuilt is not None
        assert _assistant_text(rebuilt["content"]) == "hello world " * 20
        assert rebuilt["content"][0]["thinking"] == "Let me think about this carefully."
        assert rebuilt["content"][2]["input"] == {"path": "/a"}, rebuilt["content"][2]
        assert rebuilt["stop_reason"] == "end_turn"
        kind, reason, _ = classify_response(
            json.dumps(rebuilt, ensure_ascii=False).encode(), {"max_tokens": 64}
        )
        assert kind == "ok", (kind, reason)
        # Tee: truncated stream (EOF before message_delta) → hard missing_stop_reason
        trunc_sse = (
            sse("message_start", {"type": "message_start", "message": {
                "content": [], "usage": {"input_tokens": 5000, "output_tokens": 1}}})
            + sse("content_block_start", {"type": "content_block_start", "index": 0,
                  "content_block": {"type": "text", "text": ""}})
            + sse("content_block_delta", {"type": "content_block_delta", "index": 0,
                  "delta": {"type": "text_delta", "text": "partial output"}})
            + sse("content_block_stop", {"type": "content_block_stop", "index": 0})
        )
        p2, a2 = _SSEParser(), _MessageAssembler()
        for ev_name, data_s, _raw in p2.feed(trunc_sse):
            a2.handle(ev_name, json.loads(data_s))
        assert p2.flush_end() is None
        m2 = a2.message()
        kind, reason, _ = classify_response(
            json.dumps(m2).encode(), {"max_tokens": 4096}
        )
        assert kind == "hard" and reason == "missing_stop_reason", (kind, reason)
        # stop_reason=max_tokens is a complete stream → classify ok (current semantics)
        m2["stop_reason"] = "max_tokens"
        kind, reason, _ = classify_response(
            json.dumps(m2).encode(), {"max_tokens": 4096}
        )
        assert kind == "ok", (kind, reason)
        # Tee continuation: index remap stays dense when thinking block is skipped
        remap = _IndexRemap(2)  # 2 accumulated blocks from previous round
        assert remap.start(0) == 2   # first forwarded block (was upstream idx 0)
        assert remap.start(2) == 3   # upstream idx 1 was a skipped thinking block
        assert remap.get(2) == 3
        # Tee continuation: first text block buffer dedup against acc tail
        acc_tail = "The quick brown fox jumps over the lazy dog and sleeps"
        first_buf = "over the lazy dog and sleeps until morning comes"
        assert _dedup_overlap(acc_tail, first_buf) == " until morning comes"
        assert _dedup_overlap(acc_tail, "brand new text") == "brand new text"
        # Accumulative merge chain: merge(merge(a,b),c) text + token sums
        ma = {"content": [{"type": "text", "text": "aaa "}],
              "stop_reason": "end_turn",
              "usage": {"input_tokens": 10, "output_tokens": 3}}
        mb = {"content": [{"type": "text", "text": "bbb "}],
              "stop_reason": "end_turn",
              "usage": {"input_tokens": 11, "output_tokens": 4}}
        mc = {"content": [{"type": "text", "text": "ccc"}],
              "stop_reason": "end_turn",
              "usage": {"input_tokens": 12, "output_tokens": 5}}
        mabc = merge_responses(merge_responses(ma, mb), mc)
        assert mabc["content"][0]["text"] == "aaa bbb ccc", mabc["content"]
        assert mabc["usage"]["output_tokens"] == 12, mabc["usage"]
        assert mabc["usage"]["input_tokens"] == 12, mabc["usage"]
        # Active request semaphore: non-blocking acquire / release
        assert _active_req_acquire() is True
        old_cnt = _active_req_count
        assert old_cnt >= 1
        _active_req_release()
        assert _active_req_count == old_cnt - 1
        # Response size limit
        assert _read_limited(io.BytesIO(b"x" * 100), 100) == b"x" * 100
        try:
            _read_limited(io.BytesIO(b"x" * 101), 100)
            assert False, "should have raised"
        except ValueError:
            pass
        # Latency recording
        _latencies.clear()
        _record_latency(42.5)
        _record_latency(99.0)
        assert len(_latencies) == 2
        assert _latencies[0] == 42.5
        # content_blocked_502 uses standard error shape
        cb = json.loads(content_blocked_502("test", 400, b"secret-key-here"))
        assert "upstream_snippet" not in cb.get("error", cb)
        assert cb["error"]["type"] == "overloaded_error", cb
        # _api_error standard shape
        ae = json.loads(_api_error(502, "test"))
        assert ae["error"]["type"] == "overloaded_error"
        ae2 = json.loads(_api_error(429, "rate"))
        assert ae2["error"]["type"] == "rate_limit_error"
        # Continuation trimming
        long_msgs = [{"role": "user", "content": "sys " * 600}]
        for i in range(20):
            long_msgs.append({"role": "assistant", "content": f"a{i}"})
            long_msgs.append({"role": "user", "content": f"u{i}"})
        long_payload = {"messages": long_msgs, "max_tokens": 4096}
        trunc = {"content": [{"type": "text", "text": "truncated"}]}
        cont = build_continuation_payload(long_payload, trunc)
        cont_msgs = cont["messages"]
        assert cont_msgs[-1]["role"] == "user"
        assert TRUNC_CONTINUE_PROMPT in str(cont_msgs[-1])
        assert cont_msgs[-2]["role"] == "assistant"
        assert len(cont_msgs) <= TRUNC_CONTEXT_WINDOW + 4, f"too many: {len(cont_msgs)}"
        assert cont_msgs[0]["content"] == "sys " * 600, "system msg lost"
        # Verify alternation: no two consecutive same-role messages
        for i in range(len(cont_msgs) - 1):
            assert cont_msgs[i]["role"] != cont_msgs[i + 1]["role"], (
                f"alternation broken at {i}: {cont_msgs[i]['role']}, {cont_msgs[i+1]['role']}"
            )
        # merge_responses preserves cache tokens from first response
        r1 = {"content": [{"type": "text", "text": "a"}], "stop_reason": "end_turn",
               "usage": {"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 50}}
        r2 = {"content": [{"type": "text", "text": "b"}], "stop_reason": "end_turn",
               "usage": {"input_tokens": 100, "output_tokens": 5}}
        m = merge_responses(r1, r2)
        assert m["usage"].get("cache_read_input_tokens") == 50, m["usage"]
        # _usage_extra extracts cache fields
        ue = _usage_extra({"usage": {"input_tokens": 10, "output_tokens": 5,
                                     "cache_read_input_tokens": 42}})
        assert ue == {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 42}, ue
        assert _usage_extra(None) is None
        assert _usage_extra({"usage": {}}) is None
        # Journal rotation: creates .1 backup
        import tempfile, types
        _mod = sys.modules[__name__]
        with tempfile.TemporaryDirectory() as td:
            jp = os.path.join(td, "test.jsonl")
            old_jp, old_max = _mod.JOURNAL_PATH, _mod.JOURNAL_MAX_BYTES
            try:
                _mod.JOURNAL_PATH = jp
                _mod.JOURNAL_MAX_BYTES = 50
                with open(jp, "w") as f:
                    f.write("x" * 60 + "\n")
                _rotate_journal()
                assert os.path.exists(jp + ".1"), "rotation did not create .1"
                assert not os.path.exists(jp), "original not moved"
            finally:
                _mod.JOURNAL_PATH, _mod.JOURNAL_MAX_BYTES = old_jp, old_max
        # Metrics snapshot: save and restore
        with tempfile.TemporaryDirectory() as td:
            snap_path = os.path.join(td, "metrics.json")
            old_snap = _mod.METRICS_SNAPSHOT_PATH
            try:
                _mod.METRICS_SNAPSHOT_PATH = snap_path
                _save_metrics_snapshot()
                assert os.path.exists(snap_path), "snapshot not saved"
                with open(snap_path) as f:
                    s = json.load(f)
                assert s["port"] == LISTEN_PORT
                assert "ok" in s and "soft" in s and "hard" in s
            finally:
                _mod.METRICS_SNAPSHOT_PATH = old_snap
        # Concurrency semaphore exists
        if UPSTREAM_CONCURRENCY > 0:
            assert _upstream_sem is not None
        # TG alert: _check_hard_alert increments counter
        old_ch = _mod._consecutive_hard
        _mod._consecutive_hard = 0
        _check_hard_alert("test")
        assert _mod._consecutive_hard == 1
        _reset_hard_counter()
        assert _mod._consecutive_hard == 0
        _mod._consecutive_hard = old_ch
        # Codex spoof: verify header dict shape
        assert isinstance(_CODEX_HEADERS, dict) and "Originator" in _CODEX_HEADERS
        # AnyRouter 1M: verify beta string + header injection logic
        assert "context-1m" in _ANYROUTER_1M_BETA
        _saved_1m = ANYROUTER_1M
        try:
            _mod.ANYROUTER_1M = True
            _h = Handler.__new__(Handler)
            _h.headers = {"anthropic-beta": "prompt-caching-2024-07-31"}
            _hm = _h._header_map()
            assert _ANYROUTER_1M_BETA in _hm.get("anthropic-beta", ""), \
                f"1M beta not injected: {_hm.get('anthropic-beta')}"
            assert "prompt-caching" in _hm["anthropic-beta"], \
                "existing beta lost after 1M injection"
            _h2 = Handler.__new__(Handler)
            _h2.headers = {}
            _hm2 = _h2._header_map()
            assert _hm2.get("anthropic-beta") == _ANYROUTER_1M_BETA, \
                f"1M beta not set when no existing: {_hm2.get('anthropic-beta')}"
        finally:
            _mod.ANYROUTER_1M = _saved_1m
        print("selftest_ok")
        raise SystemExit(0)

    _load_metrics_snapshot()
    threading.Thread(target=_metrics_snapshot_loop, daemon=True).start()
    _log(f"metrics_snapshot every {METRICS_SNAPSHOT_INTERVAL}s → {METRICS_SNAPSHOT_PATH}")
    if STREAM_PASSTHROUGH:
        _log("stream_passthrough=ON (direct upstream streaming, no guard classify)")
    elif TEE:
        _log("tee=ON (live SSE relay + classify on reassembled message)")
    if MAX_ACTIVE_REQUESTS > 0:
        _log(f"max_active_requests={MAX_ACTIVE_REQUESTS}")
    if UPSTREAM_CONCURRENCY > 0:
        _log(f"upstream_concurrency={UPSTREAM_CONCURRENCY}")
    _log(f"tg_alert threshold={TG_ALERT_THRESHOLD}")
    if CODEX_SPOOF:
        _log("codex_spoof=ON (injecting Codex CLI headers for AgentRouter WAF)")
    if ANYROUTER_1M:
        _log(f"anyrouter_1m=ON (injecting anthropic-beta: {_ANYROUTER_1M_BETA})")
    ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), Handler).serve_forever()
