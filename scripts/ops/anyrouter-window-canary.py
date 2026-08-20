#!/usr/bin/env python3
"""anyrouter-window-canary — anyrouter Claude 池开窗哨兵（一次性进程，计划任务每 30min 触发）。

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
from datetime import datetime, timezone
from pathlib import Path

GUARDIAN_DIR = Path.home() / ".omp" / "guardian"
SECRETS_FILE = GUARDIAN_DIR / "secrets.json"
STATE_FILE = GUARDIAN_DIR / "anyrouter-canary-state.json"
LOG_FILE = GUARDIAN_DIR / "anyrouter-canary.log"

BRIDGE = "http://127.0.0.1:8789/v1/messages"
PROBE_MODEL = "claude-haiku-4-5-20251001"  # 池内最便宜模型；429 为全池语义
PROBE_TIMEOUT = 60
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


def probe_once() -> tuple[bool, str]:
    """单次探测，返回 (window_open, detail)。"""
    body = json.dumps({
        "model": PROBE_MODEL,
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


def probe() -> tuple[bool, str]:
    """有界多挤：最多 BURST_ATTEMPTS 次、间隔 BURST_INTERVAL 秒，首次 200 即 open。

    桥不可达属本地故障，挤无意义，直接判 closed；HTTP 错误（429/5xx）才继续挤。
    """
    last = ""
    for attempt in range(1, BURST_ATTEMPTS + 1):
        open_now, detail = probe_once()
        if open_now:
            return True, f"attempt {attempt}/{BURST_ATTEMPTS}: {detail}"
        last = detail
        if detail.startswith("bridge unreachable"):
            return False, detail
        if attempt < BURST_ATTEMPTS:
            time.sleep(BURST_INTERVAL)
    return False, f"{BURST_ATTEMPTS} attempts closed, last: {last}"


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
        log(f"Telegram 发送失败: {e}")
        return False


def main() -> int:
    secrets = load_secrets()
    last_state = ""
    try:
        last_state = json.loads(STATE_FILE.read_text(encoding="utf-8")).get("state", "")
    except (OSError, ValueError):
        pass

    open_now, detail = probe()
    state = "open" if open_now else "closed"
    log(f"probe {PROBE_MODEL}: {state} ({detail})")

    if open_now and last_state != "open":
        ok = send_telegram(
            secrets,
            "🟢 anyrouter 窗口开启\n"
            f"探测 {PROBE_MODEL} 恢复 200。\n"
            "可用法（门禁禁止自动挂链，需人工显式选用）：\n"
            "OMP 指定 anyrouter/claude-opus-5 或 anyrouter/claude-opus-4-8。\n"
            "窗口可能随时关闭（上游池负载），用后请回报结果。",
        )
        log(f"window-open alert sent={ok}")

    try:
        STATE_FILE.write_text(json.dumps({
            "state": state, "ts": datetime.now(timezone.utc).isoformat(), "detail": detail,
        }, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        log(f"状态写入失败: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
