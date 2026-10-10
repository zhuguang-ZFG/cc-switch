#!/usr/bin/env python3
"""
agentrouter-proxy — 把 agentrouter.org / ps.air-outer.com 的多 key 池暴露成
本地 OpenAI 兼容端点（模型：claude-opus-5 / claude-opus-4-8 / gpt-5.6-sol 等）。

- 上游固定带 claude-cli UA（agentrouter 按 UA 校验客户端）
- 上游顺序：ps.air-outer.com 直连（大陆可用）→ agentrouter.org（经环境代理，trust_env）
- keys.json 同级存放 {keys:[...]}，轮询 + 失败冷却（502/503/504/unavailable 冷却 180s）
- 本地鉴权：--api-key（Bearer 或 X-Api-Key）
- 流式/非流式原样透传

用法：
  python agentrouter-proxy.py --host 100.83.32.95 --port 8788 --api-key KEY --log proxy.log
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn

BASE_DIR = Path(__file__).resolve().parent
KEYS_PATH = BASE_DIR / "keys.json"
CLAUDE_UA = "claude-cli/2.1.158 (external, sdk-cli)"
UPSTREAMS = [
    os.environ.get("AIR_OUTER_BASE", "https://ps.air-outer.com/v1"),
    os.environ.get("AGENTROUTER_BASE", "https://agentrouter.org/v1"),
]
COOLDOWN_S = float(os.environ.get("KEY_COOLDOWN_S", "180"))
MAX_ATTEMPTS = 4

app = FastAPI(title="agentrouter-proxy", version="1.0")
CONFIG: dict = {"api_key": "", "log_path": None}
_LOG_LOCK = threading.Lock()
_KEY_FAIL_AT: dict = {}
_KEY_ROUND: dict = {}
_KEYS_CACHE: dict = {"mtime": 0.0, "data": []}
_KEYS_LOCK = threading.Lock()


def _log(msg: str):
    path = CONFIG.get("log_path")
    if not path:
        return
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    try:
        with _LOG_LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except OSError:
        pass


def _truncate(s: str, n: int = 120) -> str:
    s = str(s).replace("\n", " ").strip()
    return s[:n] + ("…" if len(s) > n else "")


def _keyfp(key: str) -> str:
    """key 的 8 位 SHA1 指纹：日志里用于按 key 归因故障，且不泄露密钥本身。"""
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


def _looks_like_html(b: bytes) -> bool:
    head = b[:300].lstrip().lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html") or b"aliyun_waf" in b[:4096].lower()


def _upstream_body_ok(r, stream: bool) -> bool:
    """200 但响应体非 JSON（阿里云 WAF 挑战页/空体）视作上游失败，可重试。"""
    try:
        r.json()
    except Exception:
        pass
    else:
        return True
    if stream:
        head = r.content[:64].lstrip().lower()
        if head.startswith(b"data:") or head.startswith(b"event:"):
            return True
    return not _looks_like_html(r.content)


def _check_auth(authorization: Optional[str], x_api_key: Optional[str]):
    key = CONFIG["api_key"]
    if not key:
        return
    token = ""
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    if not token and x_api_key:
        token = x_api_key
    if token != key:
        raise HTTPException(status_code=401, detail={"error": {"message": "invalid api key", "type": "auth_error"}})


def _load_keys() -> list[str]:
    try:
        mt = KEYS_PATH.stat().st_mtime
    except OSError:
        return []
    with _KEYS_LOCK:
        if mt != _KEYS_CACHE["mtime"]:
            try:
                data = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            pooled = data.get("keys") if isinstance(data, dict) else []
            keys = []
            for k in pooled or []:
                if isinstance(k, str) and k.strip() and k.strip() not in keys:
                    keys.append(k.strip())
            _KEYS_CACHE["mtime"] = mt
            _KEYS_CACHE["data"] = keys
        return _KEYS_CACHE["data"]


_QUOTA_WORDS = ("quota", "exhausted", "insufficient", "budget", "rate limit", "ratelimit", "额度", "余额")

# 个别上游 key 路由到严格校验后端（按 key 确定性复现 thinking/null-schema 400，
# 同 payload 在健康 key 上 200）。这类 400 属 key 侧缺陷而非请求缺陷：冷却换 key 重试。
# 2026-10-08 实测：keys.json key3 → 两网关均 400 content[].thinking；key1/2 → 200。
_KEYSIDE_400_WORDS = ("must be passed back", "content[].thinking", "null is not of type")

# 2026-10-10 401 分层（key 失效事故：死 key 两网关均 401，旧逻辑归 fatal 直接透传给 OMP）：
# 指纹/风控门的 401 与 key 无关（裸客户端被拒），冷却换 key 会把整池抽干——必须 fatal；
# 鉴权词命中才是 key 侧死令牌——冷却换 key。顺序：指纹门词先行排除。
_FATAL_401_WORDS = ("unauthorized client", "client detected", "cloudflare", "just a moment")
_KEYSIDE_401_WORDS = ("无效的令牌", "invalid token", "invalid api key", "invalid api-key",
                      "invalid authentication", "expired")


def _classify_401(low: str) -> str:
    if any(w in low for w in _FATAL_401_WORDS):
        return "fatal"
    if any(w in low for w in _KEYSIDE_401_WORDS):
        return "keyside"
    return "fatal"


def _is_retryable(status: int, text: str) -> bool:
    if status in (502, 503, 504, 429):
        return True
    low = (text or "").lower()
    if status == 400:
        return any(w in low for w in _KEYSIDE_400_WORDS)
    if status == 401:
        return _classify_401(low) == "keyside"
    if status in (402, 403):
        # 仅明确的额度/限流错误换 key；认证类 403（invalid key 等）必须快速失败
        return any(w in low for w in _QUOTA_WORDS)
    return any(w in low for w in ("unavailable", "temporarily", "bad gateway", "upstream"))


def _classify(status: int, text: str) -> str:
    """error kind: keyside | transient | fatal.
    keyside  = key 本身被判死（额度尽/严格后端 400/死令牌 401），换网关无用，应冷却换 key；
    transient= 网关侧抖动（5xx/429/WAF），应先同 key 试另一网关再冷却。
    2026-10-08：AIR 大面积 500 时旧逻辑把每个 key 连坐冷却、从不试 ORG，
    健康 key 被耗尽导致 400/500 透传——本分层即该根因修复。"""
    low = (text or "").lower()
    if status == 400:
        return "keyside" if any(w in low for w in _KEYSIDE_400_WORDS) else "fatal"
    if status == 401:
        return _classify_401(low)
    if status in (402, 403):
        return "keyside" if any(w in low for w in _QUOTA_WORDS) else "fatal"
    if status in (502, 503, 504, 429):
        return "transient"
    if any(w in low for w in ("unavailable", "temporarily", "bad gateway", "upstream")):
        return "transient"
    return "fatal"


def _pick_key(epoch: float) -> str | None:
    keys = _load_keys()
    if not keys:
        return None
    now = time.time()
    with _KEYS_LOCK:
        idx = _KEY_ROUND.get("g", 0) % len(keys)
        for _ in range(len(keys)):
            k = keys[idx]
            fail_at = _KEY_FAIL_AT.get(k, 0.0)
            if fail_at <= epoch or now - fail_at >= COOLDOWN_S:
                _KEY_ROUND["g"] = idx + 1
                return k
            idx = (idx + 1) % len(keys)
    return None


def _mark_fail(key: str):
    with _KEYS_LOCK:
        _KEY_FAIL_AT[key] = time.time()


def _mark_ok(key: str, epoch: float):
    with _KEYS_LOCK:
        if _KEY_FAIL_AT.get(key, 0.0) <= epoch:
            _KEY_FAIL_AT.pop(key, None)


def _headers(key: str, json_body: bool = True) -> dict:
    h = {
        "Authorization": f"Bearer {key}",
        "User-Agent": CLAUDE_UA,
        "Accept": "application/json",
    }
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _sanitize_chat_payload(payload: dict, rid: str) -> None:
    """NewAPI relay 重序列化会把 tools[].function.parameters.required 缺失变成显式
    null（deepseek 上游 400: null is not of type array），补 [] 可过。
    deepseek thinking 模式要求每条 assistant 的 reasoning_content 非空：OMP 历史
    回传大量空串会被拒（400 must be passed back），缺失/空串统一补占位串 "thinking"
    （实测占位=200，空串=400，"(elided)" 会触发上游 content-blocked 勿用）。
    已有非空 reasoning_content 不动。2026-10-08 实测两类 400 全由此来。"""
    fixes = 0
    for t in payload.get("tools") or []:
        fn = t.get("function") if isinstance(t, dict) else None
        params = fn.get("parameters") if isinstance(fn, dict) else None
        if isinstance(params, dict) and not isinstance(params.get("required"), list):
            params["required"] = []
            fixes += 1
    if "deepseek" in str(payload.get("model", "")).lower():
        for m in payload.get("messages") or []:
            if isinstance(m, dict) and m.get("role") == "assistant" and not m.get("reasoning_content"):
                m["reasoning_content"] = "thinking"
                fixes += 1
    if fixes:
        _log(f"[{rid}] ⛨ sanitized {fixes} field(s)")


async def _client() -> httpx.AsyncClient:
    # trust_env=True：HTTP_PROXY/HTTPS_PROXY 环境变量生效（agentrouter.org 走 127.0.0.1:7897）
    return httpx.AsyncClient(timeout=httpx.Timeout(300, connect=15, read=120), trust_env=True)


async def _forward(path: str, payload: dict, model: str, stream: bool, t0: float, rid: str):
    """key×网关 双层重试：同一 key 顺序试完所有网关；仅 keyside 错误立即冷却换 key，
    网关 transient 全部失败后才冷却该 key（避免单网关故障连坐健康 key）。"""
    epoch = time.time()
    last_status, last_text = 502, "no keys"
    async with await _client() as c:
        for _attempt in range(MAX_ATTEMPTS):
            key = _pick_key(epoch)
            if key is None:
                break
            transient = None
            for base in UPSTREAMS:
                tag = base.split("//", 1)[-1][:22]
                try:
                    r = await c.post(f"{base}{path}", headers=_headers(key), json=payload)
                except httpx.HTTPError as e:
                    transient = (502, str(e))
                    _log(f"[{rid}] ↻ 网络错误 | {model} | {tag} | {e}")
                    continue
                if r.status_code == 200:
                    if not _upstream_body_ok(r, stream):
                        transient = (502, "upstream 200 non-JSON (waf challenge?)")
                        _log(f"[{rid}] ↻ 200 non-json | {model} | {tag} | waf? try next")
                        continue
                    _mark_ok(key, epoch)
                    if stream:
                        return StreamingResponse(
                            _stream_forward(c, r, model, t0, rid, key, epoch),
                            media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                        )
                    _log(f"[{rid}] ◀ {model} | {path} | {time.time()-t0:.1f}s | ok")
                    return JSONResponse(content=r.json())
                text = r.text
                kind = _classify(r.status_code, text)
                last_status, last_text = r.status_code, text
                if kind == "keyside":
                    _mark_fail(key)
                    # 2026-10-08：body/fingerprint 入库——20:09/20:11 的 keyside 400 风暴
                    # 当时只记了状态码与网关，事后无法区分"哪把 key / 哪个字段"（逐 key 探针
                    # 6/6 200 也未能复现）。keyfp = key 的 sha1 前 8 位，非密钥本身。
                    _log(f"[{rid}] ↻ {r.status_code} keyside | {model} | {tag} | keyfp={_keyfp(key)} "
                         f"| key cooled | body={_truncate(text, 200)}")
                    break
                if kind == "transient":
                    transient = (r.status_code, text)
                    _log(f"[{rid}] ↻ {r.status_code} transient | {model} | {tag} | keyfp={_keyfp(key)} "
                         f"| try next upstream | body={_truncate(text, 120)}")
                    continue
                _log(f"[{rid}] ✗ HTTP {r.status_code} | {model} | {tag} | keyfp={_keyfp(key)} | {_truncate(text,120)}")
                try:
                    detail = r.json()
                except Exception:
                    detail = {"error": {"message": text[:500], "type": "upstream_error", "code": r.status_code}}
                raise HTTPException(status_code=r.status_code, detail=detail)
            if transient:
                _mark_fail(key)
                last_status, last_text = transient
                _log(f"[{rid}] ↻ 全网关 transient | {model} | keyfp={_keyfp(key)} | key cooled")
    # 2026-10-08：重试耗尽（全 key 冷却 / 尝试用尽）= 网关侧瞬时态，不是最后一个上游的
    # 终审。旧逻辑原样抛回最后一个 4xx（keyside 400/401）或默认 502：
    #   · NewAPI AutomaticRetryStatusCodes=408,500-503 —— 4xx 不在内，ch118/ch15 备链
    #     完全不触发（OMP 实测 20:09:18→20:09:45 单次会话连吃 3 次 400）；
    #   · AutomaticDisableStatusCodes=401,402,403,502 —— 默认 502 落在内，key 池耗尽
    #     （"no keys"）会连坐自动禁用 ch180。
    # 统一改 503：进重试集触发备链 failover，且不在自动禁用集。
    raise HTTPException(status_code=503,
                        detail={"error": {"message": f"all keys/upstreams exhausted: {str(last_text)[:400]}", "type": "upstream_exhausted"}})


@app.get("/health")
def health(authorization: Optional[str] = Header(default=None),
           x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key")):
    _check_auth(authorization, x_api_key)
    return {"status": "ok", "keys": len(_load_keys()), "upstreams": UPSTREAMS}


@app.get("/v1/models")
async def list_models(authorization: Optional[str] = Header(default=None),
                      x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key")):
    _check_auth(authorization, x_api_key)
    epoch = time.time()
    keys = _load_keys()
    last_err = "no keys"
    async with await _client() as c:
        for _ in range(MAX_ATTEMPTS):
            key = _pick_key(epoch)
            if key is None:
                break
            for base in UPSTREAMS:
                try:
                    r = await c.get(f"{base}/models", headers=_headers(key, False))
                    if r.status_code == 200:
                        if not _upstream_body_ok(r, False):
                            last_err = "upstream 200 non-JSON (waf challenge?)"
                            _log("↻ 200 non-json | /models | waf? try next")
                            continue
                        _mark_ok(key, epoch)
                        return JSONResponse(content=r.json())
                    last_err = f"HTTP {r.status_code}"
                    if _is_retryable(r.status_code, r.text):
                        _mark_fail(key)
                        break
                except httpx.HTTPError as e:
                    last_err = str(e)
    # 同上：/models 侧的耗尽同样是瞬时态，502 落在 NewAPI 自动禁用集（401,402,403,502）内。
    raise HTTPException(status_code=503, detail={"error": {"message": f"all upstreams failed: {last_err}", "type": "upstream_exhausted"}})


@app.post("/v1/chat/completions")
async def chat_completions(request: Request,
                           authorization: Optional[str] = Header(default=None),
                           x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key")):
    _check_auth(authorization, x_api_key)
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error": {"message": f"bad json: {e}", "type": "invalid_request_error"}})
    model = payload.get("model", "?")
    stream = bool(payload.get("stream"))
    t0 = time.time()
    rid = os.urandom(4).hex()
    _log(f"[{rid}] ▶ {model} | stream={stream} | msgs={len(payload.get('messages') or [])}")
    _sanitize_chat_payload(payload, rid)
    return await _forward("/chat/completions", payload, model, stream, t0, rid)


@app.post("/v1/responses")
async def responses_passthrough(request: Request,
                                authorization: Optional[str] = Header(default=None),
                                x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key")):
    """OpenAI Responses API 透传（Codex CLI）。gpt-6-astra 的 function tools 仅
    在 /responses 可用（chat/completions 对 tools 一律 400），故必须原生转发。"""
    _check_auth(authorization, x_api_key)
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error": {"message": f"bad json: {e}", "type": "invalid_request_error"}})
    model = payload.get("model", "?")
    stream = bool(payload.get("stream"))
    t0 = time.time()
    rid = os.urandom(4).hex()
    _log(f"[{rid}] ▶ {model} | /responses | stream={stream}")
    return await _forward("/responses", payload, model, stream, t0, rid)


async def _stream_forward(client: httpx.AsyncClient, response: httpx.Response, model: str,
                          t0: float, rid: str, key: str, epoch: float):
    """流式透传；已有内容后忽略尾部错误。"""
    prefix = f"[{rid}] "
    yielded = False
    try:
        async for chunk in response.aiter_bytes():
            if chunk:
                yielded = True
                yield chunk
    except httpx.HTTPError as e:
        _mark_fail(key)
        _log(f"{prefix}✗ 流中断 | {model} | {e}")
        if not yielded:
            yield f"data: {json.dumps({'error': {'message': str(e)[:300], 'type': 'upstream_error'}}, ensure_ascii=False)}\n\n".encode()
        return
    _mark_ok(key, epoch)
    _log(f"{prefix}◀ {model} | {time.time()-t0:.1f}s | stream ok")


def main():
    ap = argparse.ArgumentParser(description="agentrouter multi-key proxy")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--api-key", default=os.environ.get("AGENTROUTER_PROXY_KEY", ""))
    ap.add_argument("--log", default=os.environ.get("AGENTROUTER_PROXY_LOG"))
    args = ap.parse_args()
    CONFIG["api_key"] = args.api_key
    CONFIG["log_path"] = args.log

    sys.stderr.write("==== agentrouter-proxy ====\n")
    sys.stderr.write(f"上游      : {UPSTREAMS}\n")
    sys.stderr.write(f"claude UA : {CLAUDE_UA}\n")
    sys.stderr.write(f"keys      : {len(_load_keys())} 个\n")
    sys.stderr.write(f"监听      : http://{args.host}:{args.port}\n")
    sys.stderr.write("  POST /v1/chat/completions  POST /v1/responses  GET /v1/models  GET /health\n")
    if args.api_key:
        sys.stderr.write("  本地鉴权已启用\n")
    sys.stderr.write("===========================\n")
    _log("==== agentrouter-proxy 启动 ====")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
