#!/usr/bin/env python3
"""anyrouter-window-canary — anyrouter Claude 池开窗哨兵（一次性进程，计划任务每 5min 触发）。

背景（2026-08-15）：anyrouter Claude 全池慢性 429（上游 Anthropic 过载转发，
非余额问题），gpt-5.6-sol 负载上限，gemini/gpt-5-codex 无协议路径。
门禁 scripts/ops/test_omp_routes.py:487 禁止 anyrouter 进自动 fallback 链
（"upstream-429, manual-canary only"），因此唯一 sanctioned 的"用上"方式：
开窗检测 → Telegram 告警 → 人工选用（OMP 显式 anyrouter/claude-opus-5 等）。

- 探测走本地指纹桥 127.0.0.1:8789（claude-haiku-4-5-20251001，max_tokens=16，
  单次 < $0.001；429 不消耗额度）。桥自身读 secrets.json 里的上游 key。
- 多挤策略（2026-08-20，社区情报：anyrouter 429 是拥堵式、持续有界重试可挤入）：
  每轮最多 5 次尝试、间隔 10s，首次 200 即判 open；429 秒回不耗额度，
  总量有界（每 30min 至多 5 次），不构成重试风暴。桥不可达（本地故障）不挤，直接判 closed。
- sol 探测（2026-08-20 同批）：每轮附带探测 gpt-5.6-sol（chat/completions 路径，
  代理内置有界挤 8×5s，单次调用即"挤完后可用性"，不叠加 canary burst 防嵌套放大），
  独立 sol_state 状态与 closed→open 告警。
- opus-5-5 探测（2026-09-24）：haiku 控制组 2026-09-23 实证常驻满载（"429 为全池语义"证伪——
  真实 CLI 走 opus-5-5 在 haiku 429 期间成功过），用户实际用 opus-5-5。双路径探测：
  8789 桥（对照组）+ 直连 anyrouter.top（主信号——runbook 记录桥 billing build 后缀未
  全量复刻，桥形状≠真实 CLI，仅桥探针会在"只对真实指纹开窗"时永远静默）。直连形状照抄
  direct-probe.cjs 已验证头集；独立 opus_state/opus_direct_state；告警=任一 open。
- 仅 closed→open 跳变发 Telegram；凭据读 ~/.omp/guardian/secrets.json，不落日志。
- 状态文件 anyrouter-canary-state.json 防重复告警；日志 anyrouter-canary.log。
- 无任何持久状态/锁需求：计划任务触发即跑即退，崩溃由下一次触发掩盖。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

GUARDIAN_DIR = Path.home() / ".omp" / "guardian"
SECRETS_FILE = GUARDIAN_DIR / "secrets.json"
STATE_FILE = GUARDIAN_DIR / "anyrouter-canary-state.json"
LOG_FILE = GUARDIAN_DIR / "anyrouter-canary.log"

BRIDGE = "http://127.0.0.1:8789/v1/messages"
BRIDGE_CHAT = "http://127.0.0.1:8789/v1/chat/completions"
PROBE_MODEL = "claude-haiku-4-5-20251001"  # 全池控制组；2026-09-23 实证其 429 ≠ opus-5-5 关窗（常驻满载）
OPUS_MODEL = "claude-opus-5-5"  # 用户实际模型（Claude Code 直连 anyrouter.top）；窗口感知主信号
SOL_MODEL = "gpt-5.6-sol"  # 未下架：09/06 实证报「负载已经达到上限」=渠道有定义全满（get_channel_failed），与「无可用渠道」（模型不存在）不同；目录（token 分组）看不到它但路由可达，挤入窗口检测仍有效
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
DIRECT_ATTEMPTS = 3    # 直连有界挤：向真实 CLI 的多次重试抽签看齐；429/503 秒回不耗额度
DIRECT_INTERVAL = 5
DIRECT_TIMEOUT = 20   # 429/503 瞬时返回；开窗 200 的 16-token SSE 数秒内收完
PROBE_TIMEOUT = 60
SOL_TIMEOUT = 120  # 代理内置有界挤（8×5s），单次调用最坏 ~40s+ 请求时间
BURST_ATTEMPTS = 5   # 多挤：每轮最多尝试次数（429 秒回不耗额度）
BURST_INTERVAL = 10  # 多挤：尝试间隔（秒）
MAX_LOG_BYTES = 512 * 1024


def log(msg: str) -> None:
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_BYTES:
            LOG_FILE.replace(LOG_FILE.with_suffix(".log.old"))
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except OSError:
        pass  # 日志失败绝不阻断探测/告警


def load_secrets() -> dict:
    try:
        # utf-8-sig：容忍带 BOM 的 secrets.json
        return json.loads(SECRETS_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        log(f"secrets.json 读取失败: {e}")
        return {}


def probe_once(model: str) -> tuple[bool, str]:
    """单次探测，返回 (window_open, detail)。"""
    body = json.dumps({
        "model": model,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "ping"}],
    }).encode()
    req = urllib.request.Request(
        BRIDGE, data=body, method="POST",
        headers={"content-type": "application/json", "anthropic-version": "2023-06-01"},
    )
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except OSError:
            pass
        return False, f"HTTP {e.code} {detail}"
    except (OSError, ValueError) as e:
        return False, f"bridge unreachable: {e}"


def probe(model: str) -> tuple[bool, str]:
    """有界多挤：最多 BURST_ATTEMPTS 次、间隔 BURST_INTERVAL 秒，首次 200 即 open。

    桥不可达属本地故障，挤无意义，直接判 closed；HTTP 错误（429/5xx）才继续挤。
    """
    last = ""
    for attempt in range(1, BURST_ATTEMPTS + 1):
        open_now, detail = probe_once(model)
        if open_now:
            return True, f"attempt {attempt}/{BURST_ATTEMPTS}: {detail}"
        last = detail
        if detail.startswith("bridge unreachable"):
            return False, detail
        if attempt < BURST_ATTEMPTS:
            time.sleep(BURST_INTERVAL)
    return False, f"{BURST_ATTEMPTS} attempts closed, last: {last}"


def probe_sol() -> tuple[bool, str]:
    """sol 单次探测：代理内置有界挤（8×5s），一次调用即代表"挤完后的可用性"。

    不再叠加 canary 侧 burst，避免 5×40s 的嵌套放大。
    """
    body = json.dumps({
        "model": SOL_MODEL,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "ping"}],
    }).encode()
    req = urllib.request.Request(
        BRIDGE_CHAT, data=body, method="POST",
        headers={"content-type": "application/json", "authorization": "Bearer local"},
    )
    try:
        with urllib.request.urlopen(req, timeout=SOL_TIMEOUT) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except OSError:
            pass
        return False, f"HTTP {e.code} {detail}"
    except (OSError, ValueError) as e:
        return False, f"bridge unreachable: {e}"


def probe_opus_direct() -> tuple[bool, str, str]:
    """直连上游探 opus-5-5，形状照抄 anyrouter-proxy/direct-probe.cjs（已验证过指纹/认证层）。

    读 ~/.claude/settings.json 的 ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN（不落日志）。
    BASE_URL host 不含 anyrouter（cc-switch 接管改写，如 127.0.0.1:15721）时跳过，
    绝不把探针打到本地代理上。返回 (open, state, detail)，state ∈ open/closed/skipped。
    """
    try:
        env = json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8")).get("env", {})
    except (OSError, ValueError) as e:
        return False, "skipped", f"settings.json unreadable: {e}"
    base = env.get("ANTHROPIC_BASE_URL", "")
    token = env.get("ANTHROPIC_AUTH_TOKEN", "")
    host = urllib.parse.urlparse(base if "://" in base else f"https://{base}").hostname or ""
    if "anyrouter" not in host.lower():
        return False, "skipped", f"skipped: takeover active (BASE_URL host={host or '?'})"
    if not token:
        return False, "skipped", "skipped: ANTHROPIC_AUTH_TOKEN missing"
    url = f"https://{host}/v1/messages?beta=true"
    body = json.dumps({
        "model": OPUS_MODEL, "max_tokens": 16, "stream": True,
        "messages": [{"role": "user", "content": "OK"}],
    }).encode()
    last = ""
    for attempt in range(1, DIRECT_ATTEMPTS + 1):
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {token}",
            "x-api-key": token,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "claude-code-20250219,context-1m-2025-08-07,interleaved-thinking-2025-05-14",
            "anthropic-dangerous-direct-browser-access": "true",
            "user-agent": "claude-cli/2.1.267 (external, sdk-cli)",
            "x-app": "cli",
        })
        try:
            with urllib.request.urlopen(req, timeout=DIRECT_TIMEOUT) as resp:
                return True, "open", f"attempt {attempt}/{DIRECT_ATTEMPTS}: HTTP {resp.status}"
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")[:150]
            except OSError:
                detail = ""
            last = f"HTTP {e.code} {detail}"
        except (OSError, ValueError) as e:
            last = f"error: {e}"
        if attempt < DIRECT_ATTEMPTS:
            time.sleep(DIRECT_INTERVAL)
    return False, "closed", f"{DIRECT_ATTEMPTS} attempts closed, last: {last}"

def send_telegram(secrets: dict, text: str) -> bool:
    token = secrets.get("telegram_token", "")
    chat_id = secrets.get("telegram_chat_id", "")
    if not token or not chat_id:
        log("Telegram 凭据缺失，跳过告警")
        return False
    handlers = []
    proxy = secrets.get("telegram_proxy", "")
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"https": proxy, "http": proxy}))
    opener = urllib.request.build_opener(*handlers)
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload, headers={"content-type": "application/json"}, method="POST",
    )
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.status == 200
    except (OSError, ValueError) as e:
        log(f"Telegram 发送失败: {e}（检查 Clash Verge 是否在运行——telegram_proxy 走本地 Clash 端口）")
        return False


def main() -> int:
    secrets = load_secrets()
    last_state = ""
    last_opus_state = ""
    last_opus_direct_state = ""
    last_sol_state = ""
    try:
        prior = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        last_state = prior.get("state", "")
        last_sol_state = prior.get("sol_state", "")
        last_opus_state = prior.get("opus_state", "")
        last_opus_direct_state = prior.get("opus_direct_state", "")
    except (OSError, ValueError):
        pass

    open_now, detail = probe(PROBE_MODEL)
    state = "open" if open_now else "closed"
    log(f"probe {PROBE_MODEL}: {state} ({detail})")

    opus_open, opus_detail = probe(OPUS_MODEL)
    opus_state = "open" if opus_open else "closed"
    log(f"probe {OPUS_MODEL}: {opus_state} ({opus_detail})")

    direct_open, direct_state, direct_detail = probe_opus_direct()
    log(f"probe {OPUS_MODEL} direct: {direct_state} ({direct_detail})")

    sol_open, sol_detail = probe_sol()
    sol_state = "open" if sol_open else "closed"
    log(f"probe {SOL_MODEL}: {sol_state} ({sol_detail})")

    if open_now and last_state != "open":
        ok = send_telegram(
            secrets,
            "🟢 anyrouter 窗口开启\n"
            f"探测 {PROBE_MODEL} 恢复 200。\n"
            "可用法（门禁禁止自动挂链，需人工显式选用）：\n"
            "OMP 指定 anyrouter/<模型>（以 models.yml 标注为准，opus-5 已下架）。\n"
            "窗口可能随时关闭（上游池负载），用后请回报结果。",
        )
        log(f"window-open alert sent={ok}")
        if not ok:
            # 告警没送出去就不落 open 态：下一轮（5min）视为仍在翻转，重发告警
            state = "closed"

    if sol_open and last_sol_state != "open":
        ok = send_telegram(
            secrets,
            "🟢 anyrouter sol 窗口开启\n"
            f"探测 {SOL_MODEL} 挤入成功（代理有界挤 8×5s 内恢复 200）。\n"
            "可用法（门禁禁止自动挂链，需人工显式选用）：\n"
            "OMP 指定 anyrouter-sol/gpt-5.6-sol。\n"
            "窗口可能随时关闭（模型负载上限），用后请回报结果。",
        )
        log(f"sol window-open alert sent={ok}")
        if not ok:
            sol_state = "closed"  # 告警没送出去就不落 open 态：下一轮重发

    # opus 告警 = 桥或直连任一 open；跳变判定也用合并信号，避免单路径开/关抖动重复告警
    opus_prev_open = last_opus_state == "open" or last_opus_direct_state == "open"
    if (opus_open or direct_open) and not opus_prev_open:
        paths = " + ".join(
            name for flag, name in ((opus_open, "8789 桥"), (direct_open, "直连 anyrouter.top")) if flag
        )
        ok = send_telegram(
            secrets,
            "🟢 anyrouter opus-5-5 窗口开启\n"
            f"探测 {OPUS_MODEL} 恢复 200（{paths}）。\n"
            "Claude Code 直接重发提示词即可（MAX_RETRIES=15 抽签，429/503 不耗额度）。\n"
            "窗口可能随时关闭（上游池负载），用后请回报结果。",
        )
        log(f"opus window-open alert sent={ok} paths={paths}")
        if not ok:
            # 告警没送出去就不落 open 态：下一轮（5min）视为仍在翻转，重发告警
            if opus_open:
                opus_state = "closed"
            if direct_open:
                direct_state = "closed"

    try:
        STATE_FILE.write_text(json.dumps({
            "state": state, "ts": datetime.now(timezone.utc).isoformat(), "detail": detail,
            "opus_state": opus_state, "opus_ts": datetime.now(timezone.utc).isoformat(),
            "opus_detail": opus_detail,
            "opus_direct_state": direct_state,
            "opus_direct_ts": datetime.now(timezone.utc).isoformat(),
            "opus_direct_detail": direct_detail,
            "sol_state": sol_state, "sol_ts": datetime.now(timezone.utc).isoformat(),
            "sol_detail": sol_detail,
        }, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        log(f"状态写入失败: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
