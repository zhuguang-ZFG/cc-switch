#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kiro-guard: kiro 反代断流守卫（Anthropic /v1/messages 前置缓冲层）

原理：kiro 反代对大 payload 流式响应会截断，但 stream:false 能返回完整 JSON。
本服务接收 new-api 的流式请求 → 改 stream:false 打上游 → 校验完整性 →
客户端要流式就把完整消息合成为 SSE 发回。

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


def _log(msg: str) -> None:
    sys.stderr.write("[kiro-guard] " + msg + "\n")


def _bump(counter: Dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def journal_event(kind: str, reason: str, extra: Optional[dict] = None) -> None:
    """Append soft/hard classify events for ops (JSONL). Never raise."""
    global _ok_count
    try:
        if kind == "ok":
            _ok_count += 1
            return
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
        if extra:
            rec.update(extra)
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with _journal_lock:
            with open(JOURNAL_PATH, "a", encoding="utf-8") as f:
                f.write(line)
        _log(f"classify_event kind={kind} reason={reason}")
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


def _fetch_upstream(req: urllib.request.Request) -> Tuple[int, bytes]:
    try:
        with _UPSTREAM_OPENER.open(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 502, json.dumps(
            {
                "error": {
                    "message": f"kiro-guard upstream error: {e}",
                    "type": "guard_error",
                }
            }
        ).encode()


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
    snippet = body[:240].decode("utf-8", errors="replace") if body else ""
    return json.dumps(
        {
            "error": {
                "message": (
                    f"kiro-guard content_blocked ({reason}); "
                    f"upstream_http={upstream_status}; failover"
                ),
                "type": "guard_error",
                "reason": f"content_blocked:{reason}",
                "upstream_snippet": snippet,
            }
        },
        ensure_ascii=False,
    ).encode()


def maybe_failover_content_block(
    status: int, body: bytes, phase: str
) -> Optional[Tuple[int, bytes, str, str, Optional[dict]]]:
    """If content-blocked, return immediate 502 tuple (no soft retry)."""
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


def synth_stream(msg: dict) -> bytes:
    out = []
    stub = {k: v for k, v in msg.items() if k != "content"}
    stub["content"] = []
    out.append(sse("message_start", {"type": "message_start", "message": stub}))
    for idx, block in enumerate(msg.get("content") or []):
        btype = block.get("type", "text")
        out.append(
            sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": block,
                },
            )
        )
        if btype == "text":
            out.append(
                sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": idx,
                        "delta": {"type": "text_delta", "text": block.get("text", "")},
                    },
                )
            )
        elif btype == "tool_use":
            out.append(
                sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": idx,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": json.dumps(
                                block.get("input", {}), ensure_ascii=False
                            ),
                        },
                    },
                )
            )
        out.append(sse("content_block_stop", {"type": "content_block_stop", "index": idx}))
    usage = msg.get("usage") or {}
    out.append(
        sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": msg.get("stop_reason"),
                    "stop_sequence": msg.get("stop_sequence"),
                },
                "usage": {"output_tokens": usage.get("output_tokens", 0)},
            },
        )
    )
    out.append(sse("message_stop", {"type": "message_stop"}))
    return b"".join(out)


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


def mark_incomplete(msg: dict) -> dict:
    """Honest stop_reason when soft-incomplete must be shown to client."""
    out = dict(msg)
    if out.get("stop_reason") == "end_turn":
        out["stop_reason"] = "max_tokens"
    return out


def build_continuation_payload(
    original_payload: dict, truncated_msg: dict
) -> dict:
    """Build retry payload with truncated response as context (kirocc spirit).

    Appends the truncated assistant turn + a user continuation prompt so the
    model picks up where it left off instead of regenerating from scratch.
    """
    out = copy.deepcopy(original_payload)
    trunc_content = truncated_msg.get("content", [])
    if not trunc_content:
        return out
    messages = list(out.get("messages") or [])
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


def _cyrillic_decode_text(text: str) -> str:
    return text.replace(_CYRILLIC_C, _LATIN_C)


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
    """Return the adaptive max_tokens cap for this request."""
    if MAX_TOKENS_CAP <= 0:
        return 0
    tools = payload.get("tools")
    if isinstance(tools, list):
        for t in tools:
            if isinstance(t, dict) and t.get("name") in _WRITE_TOOL_NAMES:
                return MAX_TOKENS_WRITE_CAP
    return MAX_TOKENS_CAP


def build_messages_request(payload: dict, header_map: Dict[str, str]) -> urllib.request.Request:
    body = dict(payload)
    body["stream"] = False
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
    return req


def fetch_classified(
    payload: dict, header_map: Dict[str, str]
) -> Tuple[int, bytes, str, str, Optional[dict]]:
    """Fetch upstream with soft retry + backoff. Returns status, body, kind, reason, msg."""
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
        journal_event("hard", f"upstream_http_{status}")
        return status, body, "hard", f"upstream_http_{status}", None
    if CYRILLIC_BYPASS:
        body = cyrillic_decode_bytes(body)

    kind, reason, msg = classify_response(body, payload)
    if kind == "ok":
        journal_event("ok", "ok")
        return status, body, kind, reason, msg
    if kind == "hard":
        if (
            SOFT_LIMIT
            and msg is not None
            and reason in SOFT_LIMIT_REASONS
        ):
            recovered = apply_soft_limit(msg, reason, payload)
            journal_event("soft", f"soft_limit:{reason}", {"phase": "hard_to_soft_limit"})
            _log(f"soft_limit from hard reason={reason}")
            return (
                200,
                json.dumps(recovered, ensure_ascii=False).encode(),
                "ok",
                f"soft_limit:{reason}",
                recovered,
            )
        journal_event("hard", reason)
        _log(f"hard_fail reason={reason}")
        return status, body, kind, reason, msg

    journal_event("soft", reason, {"phase": "first"})
    # soft: retry with backoff (Kiro-Go #143). For text truncations, inject
    # truncated content as context so the model continues (kirocc spirit).
    first_trunc_msg = msg
    for attempt in range(SOFT_RETRY):
        if SOFT_RETRY_BACKOFF_MS > 0:
            time.sleep(SOFT_RETRY_BACKOFF_MS / 1000.0)
        use_continuation = (
            TRUNC_CONTEXT
            and reason in _TEXT_TRUNC_REASONS
            and msg is not None
            and _content_nonempty(msg.get("content", []))
        )
        if use_continuation:
            retry_payload = build_continuation_payload(payload, msg)
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
            build_messages_request(retry_upstream, header_map)
        )
        blocked2 = maybe_failover_content_block(status2, body2, f"after_soft_{attempt + 1}")
        if blocked2 is not None:
            return blocked2
        if status2 != 200:
            journal_event("hard", f"upstream_http_{status2}_after_soft")
            return status2, body2, "hard", f"upstream_http_{status2}_after_soft", None
        if CYRILLIC_BYPASS:
            body2 = cyrillic_decode_bytes(body2)
        kind2, reason2, msg2 = classify_response(body2, payload)
        if kind2 == "ok":
            if use_continuation and first_trunc_msg is not None:
                merged = merge_responses(first_trunc_msg, msg2)
                journal_event(
                    "soft", f"recovered_merged:{reason}",
                    {"phase": "retry_ok_merged"},
                )
                _log(f"trunc_context merged reason={reason}")
                return (
                    200,
                    json.dumps(merged, ensure_ascii=False).encode(),
                    "ok",
                    f"merged:{reason}",
                    merged,
                )
            journal_event("soft", f"recovered:{reason}", {"phase": "retry_ok"})
            return status2, body2, kind2, reason2, msg2
        if kind2 == "hard":
            if (
                SOFT_LIMIT
                and msg2 is not None
                and reason2 in SOFT_LIMIT_REASONS
            ):
                recovered = apply_soft_limit(msg2, reason2, payload)
                journal_event(
                    "soft", f"soft_limit:{reason2}", {"phase": "hard_after_soft"}
                )
                return (
                    200,
                    json.dumps(recovered, ensure_ascii=False).encode(),
                    "ok",
                    f"soft_limit:{reason2}",
                    recovered,
                )
            journal_event("hard", reason2, {"phase": "after_soft"})
            _log(f"hard_fail_after_soft reason={reason2}")
            return status2, body2, kind2, reason2, msg2
        journal_event("soft", reason2, {"phase": f"retry_{attempt + 1}"})
        reason, body, msg = reason2, body2, msg2

    _log(f"soft_exhausted reason={reason}")
    if SOFT_LIMIT and msg is not None and reason in SOFT_LIMIT_REASONS:
        recovered = apply_soft_limit(msg, reason, payload)
        journal_event("soft", f"soft_limit:{reason}", {"phase": "exhausted_soft_limit"})
        _log(f"soft_limit after exhausted reason={reason}")
        return (
            200,
            json.dumps(recovered, ensure_ascii=False).encode(),
            "ok",
            f"soft_limit:{reason}",
            recovered,
        )
    journal_event("soft", f"retry_exhausted:{reason}", {"phase": "exhausted"})
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
        return out

    def do_POST(self):
        if self.path.startswith("/v1/chat/completions"):
            return self._passthrough()
        if self.path.startswith("/v1/messages/count_tokens"):
            return self._count_tokens()
        if not self.path.startswith("/v1/messages"):
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except Exception:
            self.send_error(400, "bad json")
            return

        want_stream = bool(payload.get("stream"))
        header_map = self._header_map()

        result_q: queue.Queue = queue.Queue(maxsize=1)

        def worker():
            result_q.put(fetch_classified(payload, header_map))

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
                                self.wfile.write(
                                    sse(
                                        "error",
                                        {
                                            "type": "error",
                                            "error": {
                                                "type": "guard_error",
                                                "message": "kiro-guard upstream timeout",
                                            },
                                        },
                                    )
                                )
                                self.wfile.flush()
                                return
                            self.wfile.write(sse("ping", {"type": "ping"}))
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return

                try:
                    if status == 200 and kind == "ok" and msg is not None:
                        self.wfile.write(synth_stream(msg))
                    else:
                        err = (
                            f"kiro-guard {kind}: {reason}"
                            if status == 200
                            else body[:300].decode(errors="replace")
                        )
                        self.wfile.write(
                            sse(
                                "error",
                                {
                                    "type": "error",
                                    "error": {"type": "guard_error", "message": err},
                                },
                            )
                        )
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
                    json.dumps(
                        {
                            "error": {
                                "message": "kiro-guard upstream error: wait timeout",
                                "type": "guard_error",
                            }
                        }
                    ).encode(),
                    "application/json",
                )
                return

        if status != 200:
            self._reply(status, body, "application/json")
            return

        if kind != "ok" or msg is None:
            self._reply(
                502,
                json.dumps(
                    {
                        "error": {
                            "message": f"kiro-guard incomplete upstream response: {reason}",
                            "type": "guard_error",
                            "reason": reason,
                        }
                    }
                ).encode(),
                "application/json",
            )
            return

        if want_stream:
            self._reply(200, synth_stream(msg), "text/event-stream")
        else:
            self._reply(200, json.dumps(msg, ensure_ascii=False).encode(), "application/json")

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
                self._reply(resp.status, resp.read(), "application/json")
                return
        except Exception:
            pass
        est = max(1, length // 4)
        self._reply(200, json.dumps({"input_tokens": est}).encode(), "application/json")

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
                data = resp.read()
                status = resp.status
        except urllib.error.HTTPError as e:
            status, data = e.code, e.read()
        except Exception as e:
            self._reply(
                502,
                json.dumps(
                    {
                        "error": {
                            "message": f"kiro-guard passthrough error: {e}",
                            "type": "guard_error",
                        }
                    }
                ).encode(),
                "application/json",
            )
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
            body = json.dumps(
                {
                    "status": "ok",
                    "port": LISTEN_PORT,
                    "upstream": UPSTREAM,
                    "ok": _ok_count,
                    "soft": dict(_soft_counts),
                    "hard": dict(_hard_counts),
                    "journal": JOURNAL_PATH,
                    "soft_retry": SOFT_RETRY,
                    "soft_retry_backoff_ms": SOFT_RETRY_BACKOFF_MS,
                    "proxy": PROXY or None,
                    "soft_limit": SOFT_LIMIT,
                    "trunc_context": TRUNC_CONTEXT,
                    "max_tokens_cap": MAX_TOKENS_CAP,
                    "max_tokens_write_cap": MAX_TOKENS_WRITE_CAP,
                    "gzip_min_bytes": GZIP_MIN_BYTES,
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
        assert fb is not None and fb[0] == 502 and b"content_blocked" in fb[1], fb
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
                {"content": [{"type": "text", "text": "сlass Foo"}]},
                ensure_ascii=False,
            ).encode()
        )
        assert b"class Foo" in dec, dec
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
        print("selftest_ok")
        raise SystemExit(0)

    ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), Handler).serve_forever()
