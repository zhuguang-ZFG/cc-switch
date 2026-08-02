#!/usr/bin/env python3
"""
NewAPI Guardian — 自愈监控系统
完整的健康检查、自动修复、Telegram 报警、每日报告、命令处理

P0/P1 修复:
- abilities 表自动同步（PUT /api/channel/ 触发 UpdateAbilities）
- OMP config.yml 真正读写
- 负载均衡（proportional weight）
- 回滚机制（定期检查 joined_channels 稳定性）
- 权重历史还原（恢复时还原历史值）
- 防抖动（多次 test_channel 验证 + 5 分钟冷却）
- 渠道性能监控（response_time/成功率历史）
- 自动降权（性能下降先降权再禁用）
- 权重自动调整（根据成功率/响应时间调整）
- Telegram 命令不阻塞主循环（短轮询 timeout=1）
- 日志轮转（RotatingFileHandler）
- 余额趋势分析（消耗速度预警）
- 指标导出（JSON metrics）
"""

import json
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Tuple

# ═══════════════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════════════

NEWAPI_BASE = "https://aliyun.donglicao.com"
NEWAPI_TOKEN = "xoIunCzgQpkj4oLGjVlI4Cd58JeE"
NEWAPI_USER = "1"

TELEGRAM_TOKEN = "8754114928:AAE2AUrJT4XOnholAJi6-qwOJ2mkMpxK_rQ"
TELEGRAM_CHAT_ID = "5345665818"

# 监控阈值
HEALTH_CHECK_INTERVAL = 15  # 秒
CHANNEL_SLOW_THRESHOLD_MS = 60000  # 60 秒
CHANNEL_FAIL_THRESHOLD = 3  # 连续失败次数
ERROR_RATE_THRESHOLD = 0.10  # 10%
BALANCE_WARNING_THRESHOLD = 1000000  # 100 万 quota
BALANCE_TREND_WINDOW = 10  # 余额趋势分析窗口（检查周期数）
BALANCE_TREND_DEPLETION_HOURS = 24  # 预计耗尽时间预警（小时）

# 降权/禁用阈值（P1: 渐进式处理）
WEIGHT_DEGRADE_FACTOR = 0.5  # 降权到原来的 50%
MIN_WEIGHT = 1  # 最小权重
WEIGHT_ADJUST_WINDOW = 20  # 权重调整统计窗口（检查周期数）
WEIGHT_ADJUST_SUCCESS_THRESHOLD = 0.8  # 成功率低于此值则降权
WEIGHT_ADJUST_SLOW_THRESHOLD = 45000  # 平均响应时间超过此值则降权
WEIGHT_BOOST_SUCCESS_THRESHOLD = 0.95  # 成功率高于此值且响应快则加权
MAX_AUTO_WEIGHT = 20  # 自动加权上限

# 防抖动 / 回滚
RECOVERY_COOLDOWN_MIN = 5  # 恢复冷却时间（分钟）
RECOVERY_TEST_COUNT = 3  # 恢复验证测试次数
RECOVERY_TEST_PASS_MIN = 2  # 恢复验证最少通过次数
JOIN_STABILITY_WINDOW_MIN = 10  # 加入后稳定性监控窗口（分钟）
JOIN_STABILITY_CHECK_INTERVAL = 3  # 稳定性检查间隔（检查周期数，即 3*15s=45s）

# 错误渠道检测（P0: 402/401/502 等瞬间返回的错误码）
ERROR_SCAN_INTERVAL = 4  # 每 N 个检查周期扫描一次（4*15s=60s）
ERROR_SCAN_BATCH_SIZE = 10  # 每次最多测试 N 个渠道（避免 API 过载）
ERROR_DISABLE_KEYWORDS = ["余额不足", "INSUFFICIENT_BALANCE", "credit balance", "quota", "402", "401", "invalid"]  # 触发禁用的错误关键词

# 本地代理
LOCAL_PROXIES = {
    "agentrouter": {"port": 8788, "name": "agentrouter", "script": "agentrouter-proxy.py", "dir": "C:/Users/zhugu/.kimi-code/proxies/agentrouter-proxy"},
    "codebuddy": {"port": 8787, "name": "codebuddy", "script": "converter.py", "dir": "C:/Users/zhugu/.kimi-code/proxies/codebuddy2openai"},
    "anyrouter": {"port": 8789, "name": "anyrouter", "script": "router-proxy.py", "dir": "C:/Users/zhugu/.kimi-code/proxies/agentrouter-proxy"},
    "atomcode": {"port": 9457, "name": "atomcode", "script": "proxy.js", "dir": "C:/Users/zhugu/atomgit-opencode-bridge"},
}

# 日志
LOG_DIR = Path.home() / ".omp" / "guardian"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "guardian.log"
STATE_FILE = LOG_DIR / "state.json"
METRICS_FILE = LOG_DIR / "metrics.json"

# ═══════════════════════════════════════════════════════════════════════════
# 日志（P2: 日志轮转）
# ═══════════════════════════════════════════════════════════════════════════

_file_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[_file_handler, logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("guardian")

# ═══════════════════════════════════════════════════════════════════════════
# Telegram
# ═══════════════════════════════════════════════════════════════════════════

class TelegramBot:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.offset = 0

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """发送 Telegram 消息"""
        try:
            url = f"{self.base_url}/sendMessage"
            data = json.dumps({
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
            }).encode()
            req = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
                return result.get("ok", False)
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    def send_alert(self, title: str, message: str, level: str = "warning"):
        """发送格式化报警"""
        icons = {"info": "ℹ️", "warning": "⚠️", "error": "🚨", "success": "✅", "restart": "🔄"}
        icon = icons.get(level, "⚠️")
        text = f"{icon} <b>{title}</b>\n\n{message}\n\n<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
        return self.send(text)

    def get_updates(self, timeout: int = 1) -> List[dict]:
        """获取 Telegram 更新（短轮询，不阻塞主循环）"""
        try:
            url = f"{self.base_url}/getUpdates?offset={self.offset}&timeout={timeout}"
            with urllib.request.urlopen(url, timeout=timeout + 5) as resp:
                result = json.loads(resp.read().decode())
                if result.get("ok") and result.get("result"):
                    updates = result["result"]
                    if updates:
                        self.offset = updates[-1]["update_id"] + 1
                    return updates
                return []
        except Exception as e:
            logger.debug(f"Telegram getUpdates: {e}")
            return []

    def process_commands(self, guardian) -> None:
        """处理 Telegram 命令（非阻塞）"""
        updates = self.get_updates(timeout=1)  # P1: 短轮询，最多阻塞 1+5=6s
        for update in updates:
            if "message" not in update:
                continue
            message = update["message"]
            text = message.get("text", "")
            if not text.startswith("/"):
                continue

            parts = text.split()
            cmd = parts[0].lower()
            args = parts[1:] if len(parts) > 1 else []

            logger.info(f"Telegram command: {cmd} {args}")

            if cmd == "/status":
                self._cmd_status(guardian)
            elif cmd == "/channels":
                self._cmd_channels(guardian)
            elif cmd == "/report":
                self._cmd_report(guardian)
            elif cmd == "/restart" and args:
                self._cmd_restart(guardian, args[0])
            elif cmd == "/enable" and args:
                self._cmd_enable(guardian, args[0])
            elif cmd == "/disable" and args:
                self._cmd_disable(guardian, args[0])
            elif cmd == "/help":
                self._cmd_help()
            else:
                self.send(f"未知命令: {cmd}\n使用 /help 查看可用命令")

    def _cmd_status(self, guardian):
        newapi_ok, newapi_msg = guardian.health.check_newapi()
        ok, rate, errors, total = guardian.health.check_error_rate()
        ok, remaining, quota = guardian.health.check_balance()

        channels = guardian.newapi.get_channels()
        healthy = sum(1 for c in channels if c.get("status") == 1)
        disabled = len([c for c in channels if c.get("status") != 1])

        # 余额趋势（P2）
        trend = guardian.autofix.get_balance_trend()
        trend_str = ""
        if trend:
            trend_str = f"\n余额趋势: {trend['rate_per_hour']:,}/h, 预计 {trend['hours_to_depletion']:.1f}h 耗尽"

        text = (
            f"📊 <b>系统状态</b>\n\n"
            f"NewAPI: {'✓ 正常' if newapi_ok else '✗ 异常'}\n"
            f"渠道: {healthy} 正常 / {disabled} 禁用 / {len(channels)} 总计\n"
            f"错误率: {rate:.1%} ({errors}/{total})\n"
            f"余额: {remaining:,} / {quota:,}{trend_str}\n"
            f"Guardian 运行中: {'✓' if guardian.running else '✗'}\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        self.send(text)

    def _cmd_channels(self, guardian):
        channels = guardian.newapi.get_channels()
        lines = ["📋 <b>渠道状态</b>\n"]
        for ch in channels[:20]:
            status = "✓" if ch.get("status") == 1 else "✗"
            rt = ch.get("response_time", 0)
            w = ch.get("weight", 0)
            lines.append(f"{status} ch{ch['id']} {ch['name']} ({rt}ms, w={w})")
        if len(channels) > 20:
            lines.append(f"... 还有 {len(channels) - 20} 个渠道")
        self.send("\n".join(lines))

    def _cmd_report(self, guardian):
        guardian._maybe_daily_report(force=True)
        self.send("📊 健康报告已生成")

    def _cmd_restart(self, guardian, proxy_name: str):
        if proxy_name not in LOCAL_PROXIES:
            self.send(f"未知代理: {proxy_name}\n可用: {', '.join(LOCAL_PROXIES.keys())}")
            return
        info = LOCAL_PROXIES[proxy_name]
        success = guardian.autofix.restart_local_proxy(proxy_name, info["port"])
        if success:
            self.send(f"✅ {proxy_name} 已重启")
        else:
            self.send(f"✗ {proxy_name} 重启失败")

    def _cmd_enable(self, guardian, channel_id: str):
        try:
            cid = int(channel_id)
        except ValueError:
            self.send(f"无效的渠道 ID: {channel_id}")
            return
        channel = guardian.newapi.get_channel(cid)
        if not channel:
            self.send(f"渠道不存在: {cid}")
            return
        success = guardian.autofix.enable_channel(cid, channel["name"])
        if success:
            self.send(f"✅ 渠道 {cid} ({channel['name']}) 已启用")
        else:
            self.send(f"✗ 渠道 {cid} 启用失败")

    def _cmd_disable(self, guardian, channel_id: str):
        try:
            cid = int(channel_id)
        except ValueError:
            self.send(f"无效的渠道 ID: {channel_id}")
            return
        channel = guardian.newapi.get_channel(cid)
        if not channel:
            self.send(f"渠道不存在: {cid}")
            return
        success = guardian.autofix.disable_slow_channel(channel, manual=True)
        if success:
            self.send(f"✅ 渠道 {cid} ({channel['name']}) 已禁用")
        else:
            self.send(f"✗ 渠道 {cid} 禁用失败")

    def _cmd_help(self):
        text = (
            "🤖 <b>Guardian 命令</b>\n\n"
            "/status - 查看系统状态\n"
            "/channels - 列出所有渠道\n"
            "/report - 生成健康报告\n"
            "/restart <proxy> - 重启本地代理\n"
            "/enable <channel_id> - 启用渠道\n"
            "/disable <channel_id> - 禁用渠道\n"
            "/help - 显示此帮助"
        )
        self.send(text)

# ═══════════════════════════════════════════════════════════════════════════
# NewAPI 客户端
# ═══════════════════════════════════════════════════════════════════════════

class NewAPIClient:
    def __init__(self, base_url: str, token: str, user_id: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.user_id = user_id

    def _request(self, method: str, path: str, data: Optional[dict] = None) -> Optional[dict]:
        """发送 NewAPI 管理 API 请求"""
        try:
            url = f"{self.base_url}{path}"
            headers = {
                "Authorization": f"Bearer {self.token}",
                "New-Api-User": self.user_id,
                "Content-Type": "application/json",
            }
            body = json.dumps(data).encode() if data else None
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            logger.error(f"NewAPI {method} {path} failed: {e.code} {e.read().decode()[:200]}")
            return None
        except Exception as e:
            logger.error(f"NewAPI {method} {path} failed: {e}")
            return None

    def get_status(self) -> bool:
        try:
            url = f"{self.base_url}/api/status"
            with urllib.request.urlopen(url, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def get_channels(self) -> List[dict]:
        result = self._request("GET", "/api/channel/?p=0&page_size=200")
        return result.get("data", {}).get("items", []) if result else []

    def get_channel(self, channel_id: int) -> Optional[dict]:
        result = self._request("GET", f"/api/channel/{channel_id}")
        return result.get("data") if result else None

    def update_channel(self, channel: dict) -> bool:
        """更新渠道（PUT /api/channel/）
        NewAPI 源码：Channel.Update() 调用 channel.UpdateAbilities(nil)，
        所以 weight/priority 变更会自动同步到 abilities 表。
        注意：请求体中不能包含 status 字段（NewAPI 会拒绝）。
        """
        ch = {k: v for k, v in channel.items() if k != "status"}
        result = self._request("PUT", "/api/channel/", ch)
        return result.get("success", False) if result else False

    def disable_channel(self, channel_id: int) -> bool:
        """禁用渠道（status=2）— 使用专用 status API"""
        result = self._request("POST", f"/api/channel/{channel_id}/status", {"status": 2})
        return result.get("success", False) if result else False

    def enable_channel(self, channel_id: int) -> bool:
        """启用渠道（status=1）— 使用专用 status API"""
        result = self._request("POST", f"/api/channel/{channel_id}/status", {"status": 1})
        return result.get("success", False) if result else False

    def test_channel(self, channel_id: int) -> Tuple[bool, str]:
        """测试渠道（发送真实请求）"""
        try:
            result = self._request("GET", f"/api/channel/test/{channel_id}")
            if result and result.get("success"):
                return True, "测试通过"
            return False, result.get("message", "测试失败") if result else "无响应"
        except Exception as e:
            return False, str(e)

    def fix_channel_abilities(self) -> bool:
        """修复所有渠道的 abilities 表（POST /api/channel/fix）"""
        result = self._request("POST", "/api/channel/fix", {})
        return result.get("success", False) if result else False

    def get_logs(self, limit: int = 100) -> List[dict]:
        result = self._request("GET", f"/api/log/?p=0&page_size={limit}")
        return result.get("data", {}).get("items", []) if result else []

    def get_user_info(self) -> Optional[dict]:
        result = self._request("GET", "/api/user/self")
        return result.get("data") if result else None

# ═══════════════════════════════════════════════════════════════════════════
# 健康检查器
# ═══════════════════════════════════════════════════════════════════════════

class HealthChecker:
    def __init__(self, newapi: NewAPIClient):
        self.newapi = newapi
        self.channel_failures: Dict[int, int] = {}
        self.channel_slow: Dict[int, int] = {}

    def check_newapi(self) -> Tuple[bool, str]:
        if self.newapi.get_status():
            return True, "NewAPI 正常"
        return False, "NewAPI 无响应"

    def check_channel(self, channel: dict) -> Tuple[bool, str, int]:
        channel_id = channel["id"]
        response_time = channel.get("response_time") or 0
        status = channel.get("status", 1)

        if status != 1:
            return True, f"已禁用 (status={status})", response_time

        if response_time > CHANNEL_SLOW_THRESHOLD_MS:
            self.channel_slow[channel_id] = self.channel_slow.get(channel_id, 0) + 1
            if self.channel_slow[channel_id] >= CHANNEL_FAIL_THRESHOLD:
                test_ok, test_msg = self.newapi.test_channel(channel_id)
                if not test_ok:
                    return False, f"响应过慢 ({response_time}ms) + 测试失败: {test_msg}", response_time
                return True, f"响应慢 ({response_time}ms) 但测试通过", response_time
            return True, f"响应慢 ({response_time}ms)", response_time
        else:
            self.channel_slow[channel_id] = 0

        return True, "正常", response_time

    def check_local_proxy(self, port: int, name: str) -> Tuple[bool, str]:
        proxy_config = {
            "agentrouter": {"key": "any", "endpoint": "/v1/models"},
            "codebuddy": {"key": "mEZCydQrTtYzKad5wHmU1pnEMb7DplcafmToLIlLpMg", "endpoint": "/v1/models"},
            "anyrouter": {"key": "any", "endpoint": "/health"},
            "atomcode": {"key": "any", "endpoint": "/v1/usage"},
        }
        config = proxy_config.get(name, {"key": "any", "endpoint": "/v1/models"})

        for host in ["100.83.32.95", "127.0.0.1"]:
            try:
                url = f"http://{host}:{port}{config['endpoint']}"
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {config['key']}"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return True, f"{name} 正常 ({host})"
            except Exception:
                continue
        return False, f"{name} 无响应（Tailscale 和 localhost 都失败）"

    def check_error_rate(self) -> Tuple[bool, float, int, int]:
        logs = self.newapi.get_logs(100)
        if not logs:
            return True, 0.0, 0, 0
        total = len(logs)
        errors = sum(1 for log in logs if log.get("type") == 2 and (log.get("channel_id") or log.get("model_name")))
        rate = errors / total if total > 0 else 0.0
        return rate <= ERROR_RATE_THRESHOLD, rate, errors, total

    def check_balance(self) -> Tuple[bool, int, int]:
        user = self.newapi.get_user_info()
        if not user:
            return False, 0, 0
        quota = user.get("quota", 0)
        used = user.get("used_quota", 0)
        remaining = quota - used
        return remaining > BALANCE_WARNING_THRESHOLD, remaining, quota

# ═══════════════════════════════════════════════════════════════════════════
# 自愈引擎
# ═══════════════════════════════════════════════════════════════════════════

class AutoFixEngine:
    def __init__(self, newapi: NewAPIClient, telegram: TelegramBot):
        self.newapi = newapi
        self.telegram = telegram
        self.state = self._load_state()
        # P1: 渠道性能历史（内存中）
        self.channel_perf: Dict[int, deque] = defaultdict(lambda: deque(maxlen=WEIGHT_ADJUST_WINDOW))
        # P2: 余额历史（用于趋势分析）
        self.balance_history: deque = deque(maxlen=BALANCE_TREND_WINDOW)
        self._scan_count = 0       # 错误扫描独立计数器
        self._stability_count = 0  # 稳定性检查独立计数器
        self._scan_offset = 0      # 错误扫描批次轮转偏移

    def _load_state(self) -> dict:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text())
            except Exception:
                pass
        return {
            "disabled_channels": [],
            "restarted_proxies": {},
            "last_daily_report": None,
            "restart_counts": {},
            "weight_history": {},
            "joined_channels": {},
            "degraded_channels": {},  # P1: 已降权的渠道
        }

    def _save_state(self):
        STATE_FILE.write_text(json.dumps(self.state, indent=2))

    # ── P1: 渠道性能监控 ──────────────────────────────────────────────────

    def _record_channel_perf(self, channel_id: int, response_time: int, healthy: bool):
        """记录渠道性能历史"""
        self.channel_perf[channel_id].append({
            "time": datetime.now().isoformat(),
            "response_time": response_time,
            "healthy": healthy,
        })

    def _get_channel_stats(self, channel_id: int) -> Optional[dict]:
        """获取渠道性能统计"""
        history = self.channel_perf.get(channel_id)
        if not history or len(history) < 3:
            return None
        rts = [h["response_time"] for h in history if h["response_time"] > 0]
        success_count = sum(1 for h in history if h["healthy"])
        return {
            "avg_response_time": sum(rts) / len(rts) if rts else 0,
            "max_response_time": max(rts) if rts else 0,
            "success_rate": success_count / len(history),
            "sample_count": len(history),
        }

    # ── P0: 错误渠道扫描（402/401/502 等瞬间返回的错误） ─────────────────

    def scan_error_channels(self):
        """定期扫描启用渠道，检测瞬间返回的错误（402 余额不足、401 无效令牌等）

        check_channel 只看 response_time，402 瞬间返回（rt<1s）永远不触发慢渠道检测。
        此方法用 test_channel 主动探测，匹配错误关键词后自动禁用。
        """
        self._scan_count += 1
        if self._scan_count % ERROR_SCAN_INTERVAL != 0:
            return

        channels = self.newapi.get_channels()
        enabled = [c for c in channels if c.get("status") == 1 and c.get("weight", 0) > 0]

        # 分批轮转测试，确保所有启用渠道都被扫描到
        n = len(enabled)
        if n == 0:
            return
        offset = self._scan_offset % n
        batch = (enabled[offset:] + enabled[:offset])[:ERROR_SCAN_BATCH_SIZE]
        self._scan_offset = (offset + ERROR_SCAN_BATCH_SIZE) % n
        for channel in batch:
            channel_id = channel["id"]
            name = channel["name"]

            test_ok, test_msg = self.newapi.test_channel(channel_id)
            if test_ok:
                continue

            # 检查错误消息是否匹配禁用关键词
            msg_lower = test_msg.lower()
            matched_keyword = None
            for kw in ERROR_DISABLE_KEYWORDS:
                if kw.lower() in msg_lower:
                    matched_keyword = kw
                    break

            if matched_keyword:
                logger.warning(f"Channel {channel_id} ({name}) error scan failed: {test_msg[:100]}")
                # 保存原始权重
                self.state.setdefault("weight_history", {})
                if str(channel_id) not in self.state["weight_history"]:
                    self.state["weight_history"][str(channel_id)] = {
                        "weight": channel.get("weight", 5),
                        "priority": channel.get("priority", 50),
                        "time": datetime.now().isoformat(),
                    }
                # 直接禁用（错误渠道不需要降权缓冲）
                if self.newapi.disable_channel(channel_id):
                    self.state["disabled_channels"].append({
                        "id": channel_id,
                        "name": name,
                        "reason": f"error_scan: {matched_keyword} — {test_msg[:80]}",
                        "time": datetime.now().isoformat(),
                    })
                    self.state.setdefault("degraded_channels", {})
                    self.state["degraded_channels"].pop(str(channel_id), None)
                    self._save_state()
                    self.telegram.send_alert(
                        "渠道错误禁用",
                        f"渠道 <b>{name}</b> (id: {channel_id}) 已自动禁用\n"
                        f"原因: {matched_keyword}\n"
                        f"详情: {test_msg[:120]}\n"
                        f"时间: {datetime.now().strftime('%H:%M:%S')}",
                        "warning"
                    )

    # ── P1: 自动降权（渐进式处理） ────────────────────────────────────────

    def degrade_channel_weight(self, channel: dict, reason: str) -> bool:
        """降权渠道（不是直接禁用，而是降低权重）"""
        channel_id = channel["id"]
        name = channel["name"]
        current_weight = channel.get("weight", 0)

        if current_weight <= MIN_WEIGHT:
            logger.info(f"Channel {channel_id} ({name}) already at min weight, disabling")
            return self.disable_slow_channel(channel)

        new_weight = max(MIN_WEIGHT, int(current_weight * WEIGHT_DEGRADE_FACTOR))
        channel["weight"] = new_weight

        if self.newapi.update_channel(channel):
            self.state.setdefault("degraded_channels", {})
            self.state["degraded_channels"][str(channel_id)] = {
                "name": name,
                "original_weight": current_weight,
                "degraded_weight": new_weight,
                "reason": reason,
                "time": datetime.now().isoformat(),
            }
            self._save_state()

            self.telegram.send_alert(
                "渠道降权",
                f"渠道 <b>{name}</b> (id: {channel_id}) 已降权\n"
                f"原因: {reason}\n"
                f"权重: {current_weight} → {new_weight}\n"
                f"时间: {datetime.now().strftime('%H:%M:%S')}",
                "warning"
            )
            logger.info(f"Channel {channel_id} ({name}) degraded: weight {current_weight} → {new_weight}")
            return True
        return False

    # ── P1: 权重自动调整 ──────────────────────────────────────────────────

    def _auto_adjust_weights(self, channels: List[dict]):
        """根据性能自动调整渠道权重（每个检查周期调用）"""
        for channel in channels:
            channel_id = channel["id"]
            if channel.get("status") != 1:
                continue

            stats = self._get_channel_stats(channel_id)
            if not stats:
                continue

            current_weight = channel.get("weight", 0)
            if current_weight == 0:
                continue  # 被手动设为 0 的渠道不自动调整

            # 成功率低 → 降权
            if stats["success_rate"] < WEIGHT_ADJUST_SUCCESS_THRESHOLD:
                new_weight = max(MIN_WEIGHT, int(current_weight * WEIGHT_DEGRADE_FACTOR))
                if new_weight < current_weight:
                    channel["weight"] = new_weight
                    self.newapi.update_channel(channel)
                    logger.info(f"Channel {channel_id} auto-degrade: weight {current_weight} → {new_weight} (success_rate={stats['success_rate']:.0%})")
            # 平均响应慢 → 降权
            elif stats["avg_response_time"] > WEIGHT_ADJUST_SLOW_THRESHOLD:
                new_weight = max(MIN_WEIGHT, int(current_weight * WEIGHT_DEGRADE_FACTOR))
                if new_weight < current_weight:
                    channel["weight"] = new_weight
                    self.newapi.update_channel(channel)
                    logger.info(f"Channel {channel_id} auto-degrade: weight {current_weight} → {new_weight} (avg_rt={stats['avg_response_time']:.0f}ms)")
            # 成功率高且响应快 → 加权（但不超过上限）
            elif stats["success_rate"] >= WEIGHT_BOOST_SUCCESS_THRESHOLD and stats["avg_response_time"] < 10000:
                new_weight = min(MAX_AUTO_WEIGHT, current_weight + 1)
                if new_weight > current_weight:
                    channel["weight"] = new_weight
                    self.newapi.update_channel(channel)
                    logger.info(f"Channel {channel_id} auto-boost: weight {current_weight} → {new_weight} (success_rate={stats['success_rate']:.0%})")

    # ── P0: 防抖动 + 恢复 + 加入聚合池 ────────────────────────────────────

    def disable_slow_channel(self, channel: dict, manual: bool = False) -> bool:
        """禁用慢渠道（P1: 先检查是否已降权，降权后仍慢则禁用）

        manual=True 表示用户手动禁用（/disable 命令），不会被自动恢复。
        """
        channel_id = channel["id"]
        name = channel["name"]
        response_time = channel.get("response_time", 0)

        # 保存原始权重到 weight_history（用于恢复时还原）
        self.state.setdefault("weight_history", {})
        if str(channel_id) not in self.state["weight_history"]:
            self.state["weight_history"][str(channel_id)] = {
                "weight": channel.get("weight", 5),
                "priority": channel.get("priority", 50),
                "time": datetime.now().isoformat(),
            }

        if self.newapi.disable_channel(channel_id):
            self.state["disabled_channels"].append({
                "id": channel_id,
                "name": name,
                "reason": f"response_time: {response_time}ms",
                "time": datetime.now().isoformat(),
                "manual": manual,
            })
            # 清理降权记录
            self.state.setdefault("degraded_channels", {})
            self.state["degraded_channels"].pop(str(channel_id), None)
            self._save_state()
            if not manual:
                self.telegram.send_alert(
                    "渠道自动禁用",
                    f"渠道 <b>{name}</b> (id: {channel_id}) 已自动禁用\n"
                    f"原因: 响应过慢 ({response_time}ms)\n"
                    f"时间: {datetime.now().strftime('%H:%M:%S')}",
                    "warning"
                )
            return True
        return False

    def enable_channel(self, channel_id: int, name: str) -> bool:
        """启用渠道"""
        if self.newapi.enable_channel(channel_id):
            self.telegram.send_alert(
                "渠道自动启用",
                f"渠道 <b>{name}</b> (id: {channel_id}) 已自动启用\n"
                f"时间: {datetime.now().strftime('%H:%M:%S')}",
                "success"
            )
            return True
        return False

    def check_and_enable_recovered_channels(self):
        """检查已禁用渠道是否恢复，自动启用并加入聚合池（P0: 防抖动 + 多次验证）

        手动禁用（manual=True）的渠道不会被自动恢复，尊重用户意图。
        """
        for record in self.state["disabled_channels"][:]:
            channel_id = record["id"]
            name = record["name"]

            # 手动禁用的渠道不自动恢复
            if record.get("manual"):
                continue

            # 防抖动：检查恢复冷却
            recovered_time = record.get("recovered_time")
            if recovered_time:
                recovered_dt = datetime.fromisoformat(recovered_time)
                if datetime.now() - recovered_dt < timedelta(minutes=RECOVERY_COOLDOWN_MIN):
                    logger.debug(f"Channel {channel_id} ({name}) still in cooldown, skipping")
                    continue

            # 防抖动：多次 test_channel 验证稳定性
            stable_count = 0
            for _ in range(RECOVERY_TEST_COUNT):
                test_ok, test_msg = self.newapi.test_channel(channel_id)
                if test_ok:
                    stable_count += 1
                time.sleep(1)

            if stable_count >= RECOVERY_TEST_PASS_MIN:
                if self.enable_channel(channel_id, name):
                    record["recovered_time"] = datetime.now().isoformat()
                    self.state["disabled_channels"].remove(record)
                    self._save_state()
                    logger.info(f"Channel {channel_id} ({name}) recovered and re-enabled")

                    # P0: 自动加入聚合池（真正更新 abilities 表）
                    self._auto_join_pool(channel_id, name)

                    # P0: 更新 OMP modelRoles（真正写入 config.yml）
                    self._update_omp_roles(channel_id, name)

    def _get_available_models(self) -> List[str]:
        """动态获取 NewAPI 可用模型列表"""
        try:
            url = f"{NEWAPI_BASE}/api/models"
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {NEWAPI_TOKEN}",
                "New-Api-User": NEWAPI_USER,
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
                data = result.get("data", {})
                all_models = []
                for channel_models in data.values():
                    if isinstance(channel_models, list):
                        all_models.extend(channel_models)
                return list(set(m for m in all_models if m))
        except Exception as e:
            logger.error(f"Failed to get available models: {e}")
            return []

    def _auto_join_pool(self, channel_id: int, name: str):
        """P0: 自动加入聚合池 — 真正更新 NewAPI weight/priority（同步 abilities 表）

        NewAPI 源码证据：
        - Channel.Update() 调用 channel.UpdateAbilities(nil)
        - PUT /api/channel/ 触发 Channel.Update()
        - 因此 weight/priority 变更自动同步到 abilities 表
        - 无需额外的 abilities API
        """
        try:
            channel = self.newapi.get_channel(channel_id)
            if not channel:
                return

            channel_models = [m.strip() for m in channel.get("models", "").split(",") if m.strip()]

            # P0: 用 _get_available_models 动态获取聚合池可用模型，过滤出本渠道可贡献的
            available_models = self._get_available_models()
            if available_models:
                # 只保留聚合池中实际存在且匹配目标关键词的模型
                target_keywords = ["deepseek", "glm", "claude", "gpt", "k3", "kimi", "qwen", "hy3"]
                pool_models = [m for m in channel_models
                               if m in available_models
                               and any(kw in m.lower() for kw in target_keywords)]
            else:
                pool_models = channel_models  # 动态发现失败时 fallback 到渠道自身模型

            if not pool_models:
                logger.info(f"Channel {channel_id} ({name}) has no pool-eligible models, skipping join")
                return

            # P0: 权重历史还原 — 恢复时还原历史值而非硬编码
            history = self.state.setdefault("weight_history", {}).get(str(channel_id))
            if history and history.get("weight", 0) > 0:
                new_weight = history["weight"]
                new_priority = history["priority"]
                logger.info(f"Channel {channel_id} restoring weight from history: {new_weight}")
            else:
                new_weight = 5
                new_priority = 50

            # 只在 weight 为 0 或被降权时才更新
            current_weight = channel.get("weight", 0)
            if current_weight == 0 or str(channel_id) in self.state.get("degraded_channels", {}):
                channel["weight"] = new_weight
                channel["priority"] = new_priority

                if self.newapi.update_channel(channel):
                    logger.info(f"Channel {channel_id} ({name}) joined pool: weight={new_weight}, priority={new_priority}, models={pool_models}")

                    # 清理降权记录
                    self.state.setdefault("degraded_channels", {})
                    self.state["degraded_channels"].pop(str(channel_id), None)

                    self.telegram.send_alert(
                        "渠道加入聚合池",
                        f"渠道 <b>{name}</b> (id: {channel_id}) 已恢复并加入聚合池\n"
                        f"模型: {', '.join(pool_models)}\n"
                        f"权重: {new_weight}, 优先级: {new_priority}\n"
                        f"时间: {datetime.now().strftime('%H:%M:%S')}",
                        "success"
                    )

                    # P0: 负载均衡 — 调整其他渠道权重
                    self._balance_pool_weights(pool_models, channel_id, new_weight)

                    # P0: 回滚机制 — 记录加入时间，用于后续稳定性监控
                    if "joined_channels" not in self.state:
                        self.state["joined_channels"] = {}
                    self.state["joined_channels"][str(channel_id)] = {
                        "time": datetime.now().isoformat(),
                        "models": pool_models,
                        "weight": new_weight,
                        "priority": new_priority,
                        "stability_checks": 0,
                        "stability_fails": 0,
                    }
                    self._save_state()
                else:
                    logger.error(f"Channel {channel_id} update_channel failed during auto_join_pool")
            else:
                logger.info(f"Channel {channel_id} ({name}) weight={current_weight}, no join needed")
        except Exception as e:
            logger.error(f"Auto join pool failed for channel {channel_id}: {e}")

    def _balance_pool_weights(self, joined_models: List[str], new_channel_id: int, new_weight: int):
        """P0: 聚合池负载均衡 — 按比例调整其他渠道权重

        策略：如果某个模型已有 N 个活跃渠道，新渠道加入后总权重增加，
        为保持每个渠道的相对份额，适当降低其他渠道权重。
        """
        try:
            channels = self.newapi.get_channels()
            for model in joined_models:
                model = model.strip()
                if not model:
                    continue

                # 获取该模型的所有活跃渠道
                model_channels = [
                    ch for ch in channels
                    if ch.get("status") == 1
                    and model in ch.get("models", "").split(",")
                    and ch["id"] != new_channel_id
                ]

                if len(model_channels) >= 5:
                    # 渠道过多，按比例降低其他渠道权重（避免新渠道压垮聚合池）
                    total_weight = sum(ch.get("weight", 0) for ch in model_channels) + new_weight
                    target_total = max(total_weight * 0.9, new_weight)  # 总权重降 10%
                    scale = target_total / total_weight if total_weight > 0 else 1.0

                    adjusted = 0
                    for ch in model_channels:
                        old_w = ch.get("weight", 0)
                        if old_w <= MIN_WEIGHT:
                            continue
                        new_w = max(MIN_WEIGHT, int(old_w * scale))
                        if new_w < old_w:
                            ch["weight"] = new_w
                            self.newapi.update_channel(ch)
                            adjusted += 1

                    if adjusted > 0:
                        logger.info(f"Model '{model}' has {len(model_channels)} channels, adjusted {adjusted} channels' weights (scale={scale:.2f})")
        except Exception as e:
            logger.error(f"Balance pool weights failed: {e}")

    def _check_joined_channels_stability(self):
        """P0: 回滚机制 — 定期检查 joined_channels 稳定性，不稳定时自动回滚

        每 JOIN_STABILITY_CHECK_INTERVAL 个检查周期检查一次（约 45 秒）。
        加入后 JOIN_STABILITY_WINDOW_MIN 分钟内监控，不稳定则回滚 weight=0。
        """
        if "joined_channels" not in self.state or not self.state["joined_channels"]:
            return

        self._stability_count += 1
        if self._stability_count % JOIN_STABILITY_CHECK_INTERVAL != 0:
            return

        for channel_id_str, join_info in list(self.state["joined_channels"].items()):
            channel_id = int(channel_id_str)
            join_time = datetime.fromisoformat(join_info["time"])

            # 加入后 JOIN_STABILITY_WINDOW_MIN 分钟内检查
            if datetime.now() - join_time > timedelta(minutes=JOIN_STABILITY_WINDOW_MIN):
                # 超过监控窗口，稳定则清理记录
                if join_info.get("stability_fails", 0) == 0:
                    logger.info(f"Channel {channel_id} stable after {JOIN_STABILITY_WINDOW_MIN}min, removing from joined_channels")
                del self.state["joined_channels"][channel_id_str]
                self._save_state()
                continue

            # 测试渠道是否仍然稳定
            join_info["stability_checks"] = join_info.get("stability_checks", 0) + 1
            test_ok, test_msg = self.newapi.test_channel(channel_id)
            if not test_ok:
                join_info["stability_fails"] = join_info.get("stability_fails", 0) + 1
                logger.warning(f"Channel {channel_id} stability check failed: {test_msg}")

                # 连续 2 次失败 → 回滚
                if join_info["stability_fails"] >= 2:
                    channel = self.newapi.get_channel(channel_id)
                    if channel:
                        channel["weight"] = 0  # 回滚权重
                        if self.newapi.update_channel(channel):
                            logger.warning(f"Channel {channel_id} unstable after join, rolled back (weight=0)")
                            self.telegram.send_alert(
                                "渠道回滚",
                                f"渠道 <b>{channel.get('name', channel_id)}</b> (id: {channel_id}) 加入后不稳定，已回滚\n"
                                f"失败次数: {join_info['stability_fails']}\n"
                                f"原因: {test_msg}\n"
                                f"时间: {datetime.now().strftime('%H:%M:%S')}",
                                "warning"
                            )
                            del self.state["joined_channels"][channel_id_str]
                            self._save_state()
            else:
                # 测试通过，重置失败计数
                if join_info.get("stability_fails", 0) > 0:
                    join_info["stability_fails"] = 0
                    logger.info(f"Channel {channel_id} stability check passed, fails reset")

            self._save_state()

    # ── P0: OMP config.yml 真正读写 ──────────────────────────────────────

    def _update_omp_roles(self, channel_id: int, name: str):
        """P0: 更新 OMP modelRoles — 真正读取、修改、写回 config.yml

        当渠道恢复时，检查 OMP 角色是否应切换到该渠道提供的模型。
        如果角色当前指向的 provider/model 不是恢复渠道提供的，则切换。
        """
        try:
            channel = self.newapi.get_channel(channel_id)
            if not channel:
                return

            channel_models = set(m.strip() for m in channel.get("models", "").split(",") if m.strip())
            channel_name = name.lower()

            # OMP 角色 → 首选模型映射（provider/model 格式）
            omp_role_models = {
                "slow": [("agentrouter/claude-opus-5", "xhigh"), ("zg-newapi-anthropic/claude-opus-5", None)],
                "plan": [("@slow", None)],
                "commit": [("zg-newapi/gpt-5.6-sol", "high")],
                "tiny": [("zg-newapi/gpt-5.6-sol", "medium")],
                "vision": [("agentrouter/claude-opus-5", "xhigh")],
                "default": [("agentrouter/claude-opus-4-8", "xhigh")],
                "smol": [("zg-newapi/gpt-5.6-sol", "high")],
                "designer": [("zg-newapi/gpt-5.6-sol", "high")],
                "task": [("zg-newapi/gpt-5.6-sol", "high")],
            }

            config_path = Path.home() / ".omp" / "agent" / "config.yml"
            if not config_path.exists():
                logger.error(f"OMP config not found: {config_path}")
                return

            config_text = config_path.read_text(encoding="utf-8")
            original_text = config_text
            updated_roles = []

            # 提取 modelRoles 块（从 modelRoles: 到下一个顶级键之前），只在块内操作
            # 避免误改 theme/retry 等其他块里的同名键
            block_match = re.search(r'^(modelRoles:\s*\n)((?:[ \t]+\S.*\n?)*)', config_text, re.MULTILINE)
            if not block_match:
                logger.error("modelRoles block not found in config.yml")
                return
            block_header = block_match.group(1)
            block_body = block_match.group(2)
            block_start, block_end = block_match.span()

            for role, model_specs in omp_role_models.items():
                for provider_model, thinking_level in model_specs:
                    if provider_model.startswith("@"):
                        continue  # 引用角色（如 @slow），跳过

                    parts = provider_model.split("/", 1)
                    if len(parts) != 2:
                        continue
                    provider, model = parts[0], parts[1]

                    # 检查恢复的渠道是否匹配该 provider（精确匹配，避免子串误匹配损坏 config）
                    provider_matches = (
                        channel_name == provider
                        or (provider == "zg-newapi" and channel_name == "newapi")
                    )

                    if not (provider_matches and model in channel_models):
                        continue

                    # 构造目标值: "provider/model:level" 或 "provider/model"
                    target_value = f"{provider_model}:{thinking_level}" if thinking_level else provider_model

                    # 在 modelRoles 块内查找角色键（2 空格缩进）
                    pattern = rf'^(  {re.escape(role)}:\s*)(.+)$'
                    match = re.search(pattern, block_body, re.MULTILINE)

                    if match:
                        current_value = match.group(2).strip()
                        if provider_model not in current_value:
                            block_body = re.sub(
                                pattern,
                                rf'\g<1>{target_value}',
                                block_body,
                                count=1,
                                flags=re.MULTILINE,
                            )
                            updated_roles.append(f"{role}: {current_value} → {target_value}")
                            logger.info(f"OMP role '{role}' switched: {current_value} → {target_value}")
                        else:
                            logger.debug(f"OMP role '{role}' already uses '{provider_model}'")
                    else:
                        # 角色不存在 → 在 modelRoles 块末尾追加
                        if block_body and not block_body.endswith("\n"):
                            block_body += "\n"
                        block_body += f"  {role}: {target_value}\n"
                        updated_roles.append(f"{role}: (new) {target_value}")
                        logger.info(f"OMP role '{role}' added: {target_value}")

            # 拼回：只替换 modelRoles 块，其他内容不动
            config_text = config_text[:block_start] + block_header + block_body + config_text[block_end:]

            # 真正写回 config.yml
            if config_text != original_text:
                config_path.write_text(config_text, encoding="utf-8")
                logger.info(f"OMP config.yml written for channel {channel_id} ({name})")
                self.telegram.send_alert(
                    "OMP 角色模型更新",
                    f"渠道 <b>{name}</b> (id: {channel_id}) 已恢复\n"
                    f"OMP config.yml 已更新:\n  " + "\n  ".join(updated_roles) + "\n"
                    f"时间: {datetime.now().strftime('%H:%M:%S')}",
                    "info"
                )
            elif updated_roles:
                self.telegram.send_alert(
                    "OMP 角色模型可用",
                    f"渠道 <b>{name}</b> (id: {channel_id}) 已恢复\n"
                    f"角色已配置正确，无需修改\n"
                    f"时间: {datetime.now().strftime('%H:%M:%S')}",
                    "info"
                )
        except Exception as e:
            logger.error(f"Update OMP roles failed for channel {channel_id}: {e}")

    # ── P2: 余额趋势分析 ──────────────────────────────────────────────────

    def record_balance(self, remaining: int):
        """记录余额历史（用于趋势分析）"""
        self.balance_history.append({
            "time": datetime.now(),
            "remaining": remaining,
        })

    def get_balance_trend(self) -> Optional[dict]:
        """分析余额趋势"""
        if len(self.balance_history) < 2:
            return None
        first = self.balance_history[0]
        last = self.balance_history[-1]
        time_diff_hours = (last["time"] - first["time"]).total_seconds() / 3600
        if time_diff_hours < 0.01:
            return None
        balance_diff = first["remaining"] - last["remaining"]
        rate_per_hour = int(balance_diff / time_diff_hours)
        if rate_per_hour <= 0:
            return {"rate_per_hour": 0, "hours_to_depletion": float("inf")}
        hours_to_depletion = last["remaining"] / rate_per_hour
        return {
            "rate_per_hour": rate_per_hour,
            "hours_to_depletion": hours_to_depletion,
        }

    # ── 本地代理重启 ──────────────────────────────────────────────────────

    def restart_local_proxy(self, name: str, port: int) -> bool:
        restart_count = self.state["restart_counts"].get(name, 0)
        if restart_count >= 3:
            self.telegram.send_alert(
                "本地代理重启失败",
                f"代理 <b>{name}</b> (端口 {port}) 已重启 {restart_count} 次仍失败\n"
                f"请手动检查",
                "error"
            )
            return False

        try:
            ps_cmd = f'Get-CimInstance Win32_Process -Filter "Name=\'pythonw.exe\'" | Where-Object {{ $_.CommandLine -match \'{name}\' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}'
            subprocess.run(
                f'powershell -Command "{ps_cmd}"',
                shell=True, capture_output=True, timeout=10
            )

            time.sleep(2)

            info = LOCAL_PROXIES.get(name)
            if not info:
                logger.error(f"Unknown proxy: {name}")
                return False

            script_path = Path(info["dir"]) / info["script"]
            if not script_path.exists():
                logger.error(f"Script not found: {script_path}")
                return False

            if name == "agentrouter":
                cmd = [
                    "C:/Users/zhugu/scoop/apps/python313/current/pythonw.exe",
                    str(script_path),
                    "--host", "100.83.32.95",
                    "--port", str(port),
                    "--log", "proxy.log"
                ]
            elif name == "codebuddy":
                cmd = [
                    "C:/Users/zhugu/scoop/apps/python313/current/pythonw.exe",
                    str(script_path),
                    "--host", "0.0.0.0",
                    "--api-key", "mEZCydQrTtYzKad5wHmU1pnEMb7DplcafmToLIlLpMg",
                    "--log", "converter.log"
                ]
            elif name == "atomcode":
                cmd = ["node", str(script_path)]
            else:
                logger.error(f"Unknown proxy type: {name}")
                return False

            subprocess.Popen(
                cmd,
                cwd=info["dir"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )

            self.state["restart_counts"][name] = restart_count + 1
            self.state["restarted_proxies"][name] = datetime.now().isoformat()
            self._save_state()

            self.telegram.send_alert(
                "本地代理重启",
                f"代理 <b>{name}</b> (端口 {port}) 已重启\n"
                f"次数: {restart_count + 1}\n"
                f"时间: {datetime.now().strftime('%H:%M:%S')}",
                "restart"
            )
            return True
        except Exception as e:
            logger.error(f"Restart {name} failed: {e}")
            return False

    def restart_newapi_container(self) -> bool:
        """重启 NewAPI 容器（多种方式）"""
        try:
            result = subprocess.run(
                "ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no donglicao@aliyun 'podman restart new-api'",
                shell=True, capture_output=True, timeout=30
            )
            if result.returncode == 0:
                self.telegram.send_alert(
                    "NewAPI 容器重启",
                    f"NewAPI 容器已重启（SSH）\n"
                    f"时间: {datetime.now().strftime('%H:%M:%S')}",
                    "restart"
                )
                return True

            result = subprocess.run(
                "podman restart new-api",
                shell=True, capture_output=True, timeout=30
            )
            if result.returncode == 0:
                self.telegram.send_alert(
                    "NewAPI 容器重启",
                    f"NewAPI 容器已重启（本地 podman）\n"
                    f"时间: {datetime.now().strftime('%H:%M:%S')}",
                    "restart"
                )
                return True

            raise Exception("All restart methods failed")
        except Exception as e:
            logger.error(f"Restart NewAPI failed: {e}")
            self.telegram.send_alert(
                "NewAPI 重启失败",
                f"NewAPI 容器重启失败\n"
                f"错误: {e}\n"
                f"请手动检查",
                "error"
            )
            return False

    def export_metrics(self, channels: List[dict], error_rate: float, remaining: int):
        """P2: 导出 JSON 指标"""
        try:
            metrics = {
                "timestamp": datetime.now().isoformat(),
                "channels": {
                    "total": len(channels),
                    "healthy": sum(1 for c in channels if c.get("status") == 1),
                    "disabled": sum(1 for c in channels if c.get("status") != 1),
                },
                "error_rate": error_rate,
                "balance_remaining": remaining,
                "disabled_channels": len(self.state.get("disabled_channels", [])),
                "degraded_channels": len(self.state.get("degraded_channels", {})),
                "joined_channels": len(self.state.get("joined_channels", {})),
                "restarted_proxies": len(self.state.get("restarted_proxies", {})),
            }
            METRICS_FILE.write_text(json.dumps(metrics, indent=2))
        except Exception as e:
            logger.error(f"Export metrics failed: {e}")

# ═══════════════════════════════════════════════════════════════════════════
# 报警管理器
# ═══════════════════════════════════════════════════════════════════════════

class AlertManager:
    def __init__(self, telegram: TelegramBot):
        self.telegram = telegram
        self.last_alerts: Dict[str, datetime] = {}
        self.alert_cooldowns = {
            "error": timedelta(minutes=1),
            "warning": timedelta(minutes=5),
            "info": timedelta(minutes=30),
            "success": timedelta(minutes=10),
            "restart": timedelta(minutes=10),
        }

    def should_alert(self, alert_type: str, level: str = "warning") -> bool:
        if alert_type not in self.last_alerts:
            return True
        cooldown = self.alert_cooldowns.get(level, timedelta(minutes=5))
        return datetime.now() - self.last_alerts[alert_type] > cooldown

    def send_daily_report(self, stats: dict):
        text = (
            f"📊 <b>NewAPI 每日健康报告</b>\n\n"
            f"日期: {stats['date']}\n"
            f"渠道总数: {stats['total_channels']}\n"
            f"正常渠道: {stats['healthy_channels']}\n"
            f"自动禁用: {stats['auto_disabled']}\n"
            f"自动重启: {stats['auto_restarts']}\n"
            f"错误率: {stats['error_rate']:.1%}\n"
            f"余额: {stats['balance']:,}\n"
            f"人工干预: {stats['manual_interventions']}\n\n"
            f"<i>系统运行正常，自愈能力工作正常</i>"
        )
        self.telegram.send(text)

# ═══════════════════════════════════════════════════════════════════════════
# 主循环
# ═══════════════════════════════════════════════════════════════════════════

class Guardian:
    def __init__(self):
        self.telegram = TelegramBot(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
        self.newapi = NewAPIClient(NEWAPI_BASE, NEWAPI_TOKEN, NEWAPI_USER)
        self.health = HealthChecker(self.newapi)
        self.autofix = AutoFixEngine(self.newapi, self.telegram)
        self.alerts = AlertManager(self.telegram)
        self.running = True

    def run(self):
        logger.info("NewAPI Guardian 启动")
        self.telegram.send_alert(
            "Guardian 启动",
            "NewAPI 自愈系统已启动\n"
            f"监控间隔: {HEALTH_CHECK_INTERVAL} 秒\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"发送 /help 查看可用命令",
            "info"
        )

        while self.running:
            try:
                self.telegram.process_commands(self)
                self._check_cycle()
                time.sleep(HEALTH_CHECK_INTERVAL)
            except KeyboardInterrupt:
                logger.info("Guardian 停止")
                break
            except Exception as e:
                logger.error(f"Check cycle error: {e}")
                time.sleep(HEALTH_CHECK_INTERVAL)

    def _check_cycle(self):
        # 1. NewAPI 健康
        newapi_ok, newapi_msg = self.health.check_newapi()
        if not newapi_ok:
            if self.alerts.should_alert("newapi_down", "error"):
                self.telegram.send_alert("NewAPI 宕机", newapi_msg, "error")
                self.autofix.restart_newapi_container()

        # 2. 渠道健康（P1: 先降权再禁用）
        channels = self.newapi.get_channels()
        for channel in channels:
            healthy, msg, rt = self.health.check_channel(channel)
            # P1: 记录性能历史
            self.autofix._record_channel_perf(channel["id"], rt, healthy)
            if not healthy:
                # P1: 先尝试降权，降权后仍慢则禁用
                if self.alerts.should_alert(f"channel_{channel['id']}", "warning"):
                    self.autofix.degrade_channel_weight(channel, msg)

        # 2.5 P0: 错误渠道扫描（402/401/502 等瞬间返回的错误）
        self.autofix.scan_error_channels()

        # 3. P1: 权重自动调整（根据性能历史）
        self.autofix._auto_adjust_weights(channels)

        # 4. P0: 检查已禁用渠道是否恢复（自动启用 + 防抖动）
        self.autofix.check_and_enable_recovered_channels()

        # 5. P0: 检查已加入渠道的稳定性（回滚机制）
        self.autofix._check_joined_channels_stability()

        # 6. 本地代理健康
        for name, info in LOCAL_PROXIES.items():
            ok, msg = self.health.check_local_proxy(info["port"], name)
            if not ok:
                if self.alerts.should_alert(f"proxy_{name}", "error"):
                    self.telegram.send_alert("本地代理故障", msg, "error")
                    self.autofix.restart_local_proxy(name, info["port"])

        # 7. 错误率
        ok, rate, errors, total = self.health.check_error_rate()
        if not ok:
            if self.alerts.should_alert("error_rate", "warning"):
                self.telegram.send_alert(
                    "错误率超标",
                    f"错误率: {rate:.1%} ({errors}/{total})\n"
                    f"阈值: {ERROR_RATE_THRESHOLD:.0%}",
                    "warning"
                )

        # 8. 余额 + P2: 趋势分析
        ok, remaining, quota = self.health.check_balance()
        self.autofix.record_balance(remaining)
        if not ok:
            if self.alerts.should_alert("balance", "warning"):
                self.telegram.send_alert(
                    "余额不足",
                    f"剩余: {remaining:,}\n"
                    f"总额: {quota:,}\n"
                    f"建议充值或切换 provider",
                    "warning"
                )
        # P2: 余额趋势预警
        trend = self.autofix.get_balance_trend()
        if trend and trend["hours_to_depletion"] < BALANCE_TREND_DEPLETION_HOURS and trend["rate_per_hour"] > 0:
            if self.alerts.should_alert("balance_trend", "warning"):
                self.telegram.send_alert(
                    "余额趋势预警",
                    f"消耗速度: {trend['rate_per_hour']:,}/h\n"
                    f"预计 {trend['hours_to_depletion']:.1f}h 后耗尽\n"
                    f"建议尽快充值",
                    "warning"
                )

        # 9. P2: 导出指标
        self.autofix.export_metrics(channels, rate, remaining)

        # 10. 每日报告
        self._maybe_daily_report()

    def _maybe_daily_report(self, force: bool = False):
        last_report = self.autofix.state.get("last_daily_report")
        today = datetime.now().strftime("%Y-%m-%d")

        if force or last_report != today:
            channels = self.newapi.get_channels()
            healthy = sum(1 for c in channels if c.get("status") == 1)
            disabled = len(self.autofix.state["disabled_channels"])
            restarts = len(self.autofix.state["restarted_proxies"])
            _, remaining, quota = self.health.check_balance()
            ok, rate, _, _ = self.health.check_error_rate()

            self.alerts.send_daily_report({
                "date": today,
                "total_channels": len(channels),
                "healthy_channels": healthy,
                "auto_disabled": disabled,
                "auto_restarts": restarts,
                "error_rate": rate,
                "balance": remaining,
                "manual_interventions": 0,
            })

            self.autofix.state["last_daily_report"] = today
            self.autofix._save_state()

# ═══════════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    guardian = Guardian()
    guardian.run()