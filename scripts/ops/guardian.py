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
import socket
import urllib.request
import urllib.error
from urllib.parse import urlparse
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Tuple

# ═══════════════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════════════

CONFIG_DIR = Path.home() / ".omp" / "guardian"
SECRETS_FILE = CONFIG_DIR / "secrets.json"
try:
    # utf-8-sig：容忍带 BOM 的 secrets.json（部分编辑器/工具保存时会加 BOM，
    #  utf-8 下 json.loads 直接失败导致全部配置静默为空）
    _SECRETS = json.loads(SECRETS_FILE.read_text(encoding="utf-8-sig"))
except (OSError, ValueError):
    _SECRETS = {}

def _config_value(env_name: str, secret_name: str, default: str = "") -> str:
    return os.environ.get(env_name) or str(_SECRETS.get(secret_name, default))

def _html_escape(text: str) -> str:
    """转义进入 Telegram HTML 的外部字符串（渠道名/错误摘要/request id 等）。"""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

NEWAPI_BASE = _config_value("NEWAPI_BASE", "newapi_base", "http://127.0.0.1:3002")
NEWAPI_TOKEN = _config_value("NEWAPI_TOKEN", "newapi_token")
NEWAPI_USER = _config_value("NEWAPI_USER", "newapi_user", "1")
TELEGRAM_TOKEN = _config_value("TELEGRAM_TOKEN", "telegram_token")
TELEGRAM_CHAT_ID = _config_value("TELEGRAM_CHAT_ID", "telegram_chat_id")
TELEGRAM_ALLOWED_USERS = set(
    u.strip()
    for u in _config_value("TELEGRAM_ALLOWED_USERS", "telegram_allowed_users", "").split(",")
    if u.strip()
)
TELEGRAM_PROXY = _config_value("TELEGRAM_PROXY", "telegram_proxy")
CODEBUDDY_API_KEY = _config_value("CODEBUDDY_API_KEY", "codebuddy_api_key")
AGENTROUTER_PROXY_KEY = _config_value("AGENTROUTER_PROXY_KEY", "agentrouter_proxy_key")
# The local clients use loopback while NewAPI reaches these proxies over
# Tailscale. Authentication and the host firewall are required when this is
# set to a wildcard address.
LOCAL_PROXY_BIND_HOST = _config_value(
    "LOCAL_PROXY_BIND_HOST", "local_proxy_bind_host", "0.0.0.0"
)
LOCAL_PROXY_PROBE_HOST = (
    "127.0.0.1" if LOCAL_PROXY_BIND_HOST in {"0.0.0.0", "::"} else LOCAL_PROXY_BIND_HOST
)

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
WEIGHT_ADJUST_WINDOW = 20  # 权重调整统计窗口（不同的 NewAPI 测试结果数）
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
WEIGHT_DEGRADE_COOLDOWN_MIN = 5  # 降权最小间隔（分钟）— 自愈节流独立于告警冷却
NEWAPI_RESTART_COOLDOWN_MIN = 30  # NewAPI 容器重启最小间隔（成功后冷却）
NEWAPI_FAIL_THRESHOLD = 3  # 连续失败次数才触发破坏性重启（防瞬态抖动）
NEWAPI_RESTART_BACKOFF_SEC = 60  # 重启失败后的退避间隔（秒），成功才进 30min 冷却

# 错误渠道检测（补充 NewAPI 内置 30min 自动测试，不重复）
# NewAPI 已内置: 每 30min 全量测试 + 401/402/403 自动禁用 + 自动启用
# Guardian 补充: 更频繁的错误码扫描（NewAPI 只在定时测试时检测）+ 本地代理 + OMP
ERROR_SCAN_INTERVAL = 20  # 每 N 个检查周期扫描一次（20*15s=5min，NewAPI 30min 太慢）
ERROR_SCAN_BATCH_SIZE = 5  # 每次最多测试 N 个渠道（降低 API 负载）
ERROR_DISABLE_KEYWORDS = ["余额不足", "INSUFFICIENT_BALANCE", "credit balance", "quota", "402", "401", "invalid"]

# 瞬态限流：HTTP 429 / rate limit / too many requests 不是渠道故障——
# 不禁用、不累计永久失败计数（交给 NewAPI 池内重试/上游退避处理）
TRANSIENT_RATE_LIMIT_MARKERS = ("429", "rate limit", "rate_limit", "too many requests")


def _is_transient_rate_limit(message: str) -> bool:
    """429 / rate limit / too many requests → 瞬态限流，非渠道故障。"""
    msg = (message or "").lower()
    return any(marker in msg for marker in TRANSIENT_RATE_LIMIT_MARKERS)

PROBE_INCOMPATIBLE_MARKERS = (
    "non_agentic_blocked",
    "only serves agentic",
    "agentic (tool-calling) clients",
)


def _is_probe_incompatible(message: str) -> bool:
    """探针形态被拒绝：无健康结论，不得据此降权或禁用渠道。"""
    msg = (message or "").lower()
    return any(marker in msg for marker in PROBE_INCOMPATIBLE_MARKERS)
TEST_CHANNEL_TIMEOUT = 15  # test_channel 独立超时（秒）：上游实测 6-30s 常见，5s 全面误报
RECOVERY_BATCH_SIZE = 2  # 每周期最多验证 N 个禁用渠道
RECOVERY_BACKOFF_BASE = 2  # 失败退避基数（分钟，NewAPI 也会自动启用，Guardian 不必太急）
RECOVERY_BACKOFF_MAX = 60  # 失败退避上限（分钟）
OMP_ROLE_CHECK_INTERVAL = 80  # 每 N 周期主动检测 OMP 角色（80*15s=20min）

# 自循环维护
FULL_SCAN_INTERVAL = 240  # 每 N 周期全量扫描（240*15s=1h，补充 NewAPI 30min 测试）
FULL_SCAN_BATCH_SIZE = 4  # 全量扫描每次测 N 个渠道（轮转）
ABILITY_FIX_INTERVAL = 480  # 每 N 周期修复 abilities 表（480*15s=2h）
STATE_CLEANUP_INTERVAL = 960  # 每 N 周期清理陈旧状态（960*15s=4h）
STATE_MAX_AGE_HOURS = 48  # 陈旧状态阈值（小时）
CYCLE_TIME_WARN_MS = 90000  # 周期耗时预警阈值（毫秒）：与 CYCLE_BUDGET_SEC 对齐——只在周期接近执行预算截断时告警；常规慢周期（探针 15s×3 + 错误扫描 5×5s + NewAPI 5s + Telegram 6s ≈ 81s）不告警
CYCLE_BUDGET_SEC = 90  # 单轮执行预算：超时后跳过剩余低优先级步骤，避免周期无限拉长

# 本地代理（anyrouter 已从 OMP disabledProviders + 本表移除：上游 anyrouter.top
# key 失效 502，进程活着但推理不可用，重启解决不了——见踩坑 10；保留进程，
# 恢复时手工加回本表即可）
# agentrouter/codebuddy 加固后只绑定 127.0.0.1 并要求 Bearer key（secrets.json）
LOCAL_PROXIES = {
    "agentrouter": {"port": 8788, "name": "agentrouter", "script": "agentrouter-proxy.py", "dir": "C:/Users/zhugu/.kimi-code/proxies/agentrouter-proxy"},
    "codebuddy": {"port": 8787, "name": "codebuddy", "script": "converter.py", "dir": "C:/Users/zhugu/.kimi-code/proxies/codebuddy2openai"},
}

# 日志
LOG_DIR = CONFIG_DIR
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "guardian.log"
METRICS_FILE = LOG_DIR / "metrics.json"
STATE_FILE = LOG_DIR / "state.json"
STATE_BACKUP_FILE = LOG_DIR / "state.json.last-good"
HEARTBEAT_FILE = LOG_DIR / "heartbeat.json"  # 心跳文件：外部 watchdog 监视新鲜度
HEARTBEAT_STALE_SEC = 180  # 心跳过期阈值（秒）：超过视为 Guardian 卡死


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
    # 同一 chat 最小发送间隔（秒），防止多告警类型同周期 burst 触发 Telegram 限流
    MIN_SEND_INTERVAL = 0.5

    def __init__(self, token: str, chat_id: str, proxy: str = ""):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.offset = 0
        self._last_send = 0.0
        self._offline_until = 0.0
        proxies = {"http": proxy, "https": proxy} if proxy else {}
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """发送 Telegram 消息（带速率限制）"""
        # 速率限制：距上次发送不足 MIN_SEND_INTERVAL 则等待
        now = time.time()
        wait = self.MIN_SEND_INTERVAL - (now - self._last_send)
        if wait > 0:
            time.sleep(wait)
        self._last_send = time.time()
        if time.time() < self._offline_until:
            return False
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
            with self.opener.open(req, timeout=5) as resp:
                result = json.loads(resp.read().decode())
                return result.get("ok", False)
        except Exception as e:
            self._offline_until = time.time() + 60
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
        if time.time() < self._offline_until:
            return []
        try:
            url = f"{self.base_url}/getUpdates?offset={self.offset}&timeout={timeout}"
            with self.opener.open(url, timeout=timeout + 2) as resp:
                result = json.loads(resp.read().decode())
                if result.get("ok") and result.get("result"):
                    updates = result["result"]
                    if updates:
                        self.offset = updates[-1]["update_id"] + 1
                    return updates
                return []
        except Exception as e:
            self._offline_until = time.time() + 60
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
            # 鉴权 1: 只接受配置的 chat_id 发送的命令，防止第三方禁用渠道/重启代理
            chat_id = str(message.get("chat", {}).get("id", ""))
            if chat_id != str(self.chat_id):
                logger.warning(f"Ignored Telegram command from unauthorized chat {chat_id}")
                continue
            # 鉴权 2: 校验发送者身份。白名单优先；未配置时仅接受私聊 owner
            #（from.id == chat.id），群组成员天然被拒，避免非授权成员执行管理命令
            sender_id = str(message.get("from", {}).get("id", ""))
            allowed_users = getattr(self, "allowed_users", TELEGRAM_ALLOWED_USERS)
            if allowed_users:
                if sender_id not in allowed_users:
                    logger.warning(
                        f"Ignored Telegram command from unauthorized sender {sender_id} "
                        f"(chat {chat_id})"
                    )
                    continue
            elif sender_id != chat_id:
                logger.warning(
                    f"Ignored Telegram command from sender {sender_id} not matching "
                    f"chat owner {chat_id} (group or spoofed)"
                )
                continue
            parts = text.split()
            cmd = parts[0].lower()
            args = parts[1:] if len(parts) > 1 else []

            logger.info(f"Telegram command: {cmd} {args}")

            try:
                if cmd == "/status":
                    self._cmd_status(guardian)
                elif cmd == "/agents":
                    self._cmd_agents(guardian)
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
                    self.send(f"未知命令: {_html_escape(cmd)}\n使用 /help 查看可用命令")
            except Exception as e:
                # 单条命令失败不中断该批后续命令（getUpdates offset 已推进）
                logger.exception(f"Telegram command {cmd} failed: {e}")

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

    def _cmd_agents(self, guardian):
        roster = guardian.get_subagent_status()
        if not roster:
            self.send("没有近期 subagent 会话")
            return
        lines = ["🤖 <b>Subagent 近期会话状态</b>\n"]
        for item in roster:
            lines.append(
                f"{_html_escape(item['status'])} {_html_escape(item['name'])} "
                f"({_html_escape(item['model'])}, {item['age_sec']}s)"
            )
        self.send("\n".join(lines))

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
        sent = guardian._maybe_daily_report(force=True)
        if sent:
            self.send("📊 健康报告已生成")
        else:
            # 发送失败即 Telegram 通道不可用（已进 offline cooldown），
            # 再发一条“失败”通知也无法送达，只记录日志待通道恢复。
            logger.error("健康报告发送失败：Telegram 通道不可用")

    def _cmd_restart(self, guardian, proxy_name: str):
        if proxy_name not in LOCAL_PROXIES:
            self.send(f"未知代理: {_html_escape(proxy_name)}\n可用: {', '.join(LOCAL_PROXIES.keys())}")
            return
        info = LOCAL_PROXIES[proxy_name]
        success = guardian.autofix.restart_local_proxy(proxy_name, info["port"])
        if success:
            self.send(f"✅ {_html_escape(proxy_name)} 已重启")
        else:
            self.send(f"✗ {_html_escape(proxy_name)} 重启失败")

    def _cmd_enable(self, guardian, channel_id: str):
        try:
            cid = int(channel_id)
        except ValueError:
            self.send(f"无效的渠道 ID: {_html_escape(channel_id)}")
            return
        channel = guardian.newapi.get_channel(cid)
        if not channel:
            self.send(f"渠道不存在: {cid}")
            return
        success = guardian.autofix.enable_channel(cid, channel["name"])
        if success:
            # 清理 disabled_channels 中的记录，防止恢复循环重复处理
            guardian.autofix.state["disabled_channels"] = [
                r for r in guardian.autofix.state.get("disabled_channels", [])
                if r["id"] != cid
            ]
            guardian.autofix._save_state()
            self.send(f"✅ 渠道 {cid} ({_html_escape(channel['name'])}) 已启用")
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
            self.send(f"✅ 渠道 {cid} ({_html_escape(channel['name'])}) 已禁用")
        else:
            self.send(f"✗ 渠道 {cid} 禁用失败")

    def _cmd_help(self):
        text = (
            "🤖 <b>Guardian 命令</b>\n\n"
            "/status - 查看系统状态\n"
            "/agents - 查看 subagent 实时状态\n"
            "/channels - 列出所有渠道\n"
            "/report - 生成健康报告\n"
            "/restart &lt;proxy&gt; - 重启本地代理\n"
            "/enable &lt;channel_id&gt; - 启用渠道\n"
            "/disable &lt;channel_id&gt; - 禁用渠道\n"
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

    def _request(self, method: str, path: str, data: Optional[dict] = None, timeout: int = 15) -> Optional[dict]:
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
            with urllib.request.urlopen(req, timeout=timeout) as resp:
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

    def test_channel(self, channel_id: int, timeout: int = TEST_CHANNEL_TIMEOUT) -> Tuple[bool, str]:
        """测试渠道（发送真实请求）— 独立短超时，避免死渠道阻塞主循环"""
        try:
            result = self._request("GET", f"/api/channel/test/{channel_id}", timeout=timeout)
            if result and result.get("success"):
                return True, "测试通过"
            return False, result.get("message", "测试失败") if result else "无响应"
        except Exception as e:
            return False, str(e)

    def fix_channel_abilities(self) -> bool:
        """修复所有渠道的 abilities 表（POST /api/channel/fix）"""
        result = self._request("POST", "/api/channel/fix", {})
        return result.get("success", False) if result else False
    def exclude_retry_status_code(self, status_code: int) -> bool:
        """Remove one status from NewAPI's channel-pool retry policy."""
        result = self._request("GET", "/api/option/")
        items = result.get("data") if result else None
        if not isinstance(items, list):
            return False
        option = next(
            (item for item in items if isinstance(item, dict) and item.get("key") == "AutomaticRetryStatusCodes"),
            None,
        )
        if option is None:
            return False

        value = str(option.get("value") or "")
        updated_parts = []
        changed = False
        for part in value.split(","):
            part = part.strip()
            if not part:
                continue
            bounds = part.split("-", 1)
            try:
                start = int(bounds[0])
                end = int(bounds[1]) if len(bounds) == 2 else start
            except ValueError:
                updated_parts.append(part)
                continue
            if not start <= status_code <= end:
                updated_parts.append(part)
                continue

            changed = True
            if start < status_code:
                updated_parts.append(str(start) if start == status_code - 1 else f"{start}-{status_code - 1}")
            if status_code < end:
                updated_parts.append(str(end) if status_code + 1 == end else f"{status_code + 1}-{end}")

        if not changed:
            return True
        response = self._request(
            "PUT",
            "/api/option/",
            {"key": "AutomaticRetryStatusCodes", "value": ",".join(updated_parts)},
        )
        return bool(response and response.get("success"))

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
        self.channel_slow: Dict[int, int] = {}
        self.channel_test_times: Dict[int, object] = {}

    def check_newapi(self) -> Tuple[bool, str]:
        if self.newapi.get_status():
            return True, "NewAPI 正常"
        return False, "NewAPI 无响应"

    def check_channel(self, channel: dict) -> Tuple[bool, str, int]:
        channel_id = channel["id"]
        response_time = channel.get("response_time") or 0
        status = channel.get("status", 1)

        if status != 1:
            self.channel_slow[channel_id] = 0
            return True, f"已禁用 (status={status})", response_time

        if response_time > CHANNEL_SLOW_THRESHOLD_MS:
            # 兜底：NewAPI 缺失 test_time 时用 sentinel 去重,避免同一份慢数据被轮询放大。
            test_time = channel.get("test_time") if channel.get("test_time") is not None else "no-test-time"
            if self.channel_test_times.get(channel_id) == test_time:
                return True, f"响应慢 ({response_time}ms，结果已计数)", response_time
            self.channel_test_times[channel_id] = test_time

            self.channel_slow[channel_id] = self.channel_slow.get(channel_id, 0) + 1
            if self.channel_slow[channel_id] >= CHANNEL_FAIL_THRESHOLD:
                self.channel_slow[channel_id] = 0
                test_ok, test_msg = self.newapi.test_channel(channel_id)
                if not test_ok:
                    return False, f"响应过慢 ({response_time}ms) + 测试失败: {test_msg}", response_time
                return True, f"响应慢 ({response_time}ms) 但测试通过", response_time
            return True, f"响应慢 ({response_time}ms)", response_time

        self.channel_slow[channel_id] = 0
        return True, "正常", response_time

    def check_local_proxy(self, port: int, name: str) -> Tuple[bool, str, bool]:
        """本地代理探针已禁用：用户反馈告警无实际作用，统一返回健康避免误报。"""
        return True, f"{name} 探针已禁用", True

    def check_local_endpoint(self, port: int, name: str) -> Tuple[bool, str]:
        """轻量存活检查：仅 TCP 连接探测端口，不发推理请求、不花上游费用。

        周期性推理探针（check_local_proxy）为省上游费用保持禁用，但重启验证
        不能复用该恒真桩——否则新进程秒退、端口未绑定也会误报"验证正常"。
        端口可连即说明进程已绑定监听，足够做存活判定。
        """
        try:
            with socket.create_connection((LOCAL_PROXY_PROBE_HOST, port), timeout=3):
                return True, f"{name} 端口 {port} 可达"
        except OSError as e:
            return False, f"{name} 端口 {port} 不可达: {e}"

    def check_error_rate(self) -> Tuple[bool, float, int, int]:
        """检查错误率

        NewAPI 日志类型（源码 model/log.go）：
        LogTypeConsume=2（正常消费）, LogTypeError=5（真正错误）。
        错误率 = 错误请求 / 实际请求数（消费+错误），而非全部日志。
        """
        logs = self.newapi.get_logs(100)
        if not logs:
            return True, 0.0, 0, 0
        errors = sum(1 for log in logs if log.get("type") == 5)
        requests = sum(1 for log in logs if log.get("type") in (2, 5))
        rate = errors / requests if requests > 0 else 0.0
        return rate <= ERROR_RATE_THRESHOLD, rate, errors, requests

    def check_balance(self) -> Tuple[bool, int, int]:
        """检查余额

        返回: (是否正常, 剩余, 总额)
        API 失败时返回 (True, -1, -1)，区别于余额真的为 0。
        """
        user = self.newapi.get_user_info()
        if not user:
            return True, -1, -1  # API 失败，不是余额问题
        quota = user.get("quota", 0)
        used = user.get("used_quota", 0)
        remaining = quota - used
        return remaining > BALANCE_WARNING_THRESHOLD, remaining, quota

# ═══════════════════════════════════════════════════════════════════════════
# 自愈引擎
# ═══════════════════════════════════════════════════════════════════════════

class AutoFixEngine:
    def __init__(self, newapi: NewAPIClient, telegram: TelegramBot, health: HealthChecker):
        self.newapi = newapi
        self.telegram = telegram
        self.health = health
        self.state = self._load_state()
        # P1: 渠道性能历史（只记录不同的 NewAPI test_time）
        self.channel_perf: Dict[int, deque] = defaultdict(lambda: deque(maxlen=WEIGHT_ADJUST_WINDOW))
        self._last_channel_tests: Dict[int, object] = {}
        # P2: 余额历史（用于趋势分析）
        self.balance_history: deque = deque(maxlen=BALANCE_TREND_WINDOW)
        self._scan_count = 0       # 错误扫描独立计数器
        self._stability_count = 0  # 稳定性检查独立计数器
        self._scan_offset = 0      # 错误扫描批次轮转偏移
        self._omp_check_count = 0  # OMP 角色主动检测计数器
        self._full_scan_count = 0  # 全量健康扫描计数器
        self._full_scan_offset = 0  # 全量扫描批次轮转偏移
        self._full_scan_failures: Dict[int, int] = {}
        self._ability_fix_count = 0  # abilities 修复计数器
        self._cleanup_count = 0    # 状态清理计数器

    def _load_state(self) -> dict:
        defaults = {
            "schema_version": 1,
            "disabled_channels": [],
            "restarted_proxies": {},
            "last_daily_report": None,
            "restart_counts": {},
            "weight_history": {},
            "joined_channels": {},
            "degraded_channels": {},
            "newapi_fail_streak": 0,
        }
        if STATE_FILE.exists():
            try:
                loaded = json.loads(STATE_FILE.read_text())
                if not isinstance(loaded, dict):
                    raise ValueError("state.json root is not an object")
                # 合并默认值，确保旧版 state.json 缺键不 KeyError
                for k, v in defaults.items():
                    if k not in loaded:
                        loaded[k] = v
                return loaded
            except (json.JSONDecodeError, ValueError) as e:
                # 仅内容损坏（解析/类型错误）才备份留证——OSError 是 I/O 问题，不搬文件
                # 独占创建（xb）防同名覆盖：ns+pid 撞名时用计数器后缀重试
                backup = None
                try:
                    raw = STATE_FILE.read_bytes()
                    base = f"state.json.corrupt-{datetime.now().strftime('%Y%m%d%H%M%S%f')}-{os.getpid()}"
                    backup = STATE_FILE.with_name(base)
                    attempt = 0
                    while True:
                        try:
                            with open(backup, "xb") as f:
                                f.write(raw)
                            break
                        except FileExistsError:
                            attempt += 1
                            backup = STATE_FILE.with_name(f"{base}-{attempt}")
                    logger.error(f"state.json corrupted, backed up to {backup}: {e}")
                except OSError as be:
                    logger.error(f"state.json corrupted AND backup failed: {e}; backup error: {be}")
                # 保留上限 5 份取证。刚写出的这份是定义上的最新一份，先摘出来不参与
                # 裁剪：名字槽会被回收（旧的无后缀名被删后，下次损坏又拿到同名），
                # 粗粒度 FS 下 mtime 全同时，纯排序会把最新一份排到最前而误删。
                created = backup if backup is not None and backup.exists() else None
                history = sorted(
                    (
                        p
                        for p in STATE_FILE.parent.glob("state.json.corrupt-*")
                        if p != created
                    ),
                    key=lambda p: (p.stat().st_mtime_ns, p.name),
                )
                keep = 5 - (1 if created is not None else 0)
                for old in history[:-keep]:
                    try:
                        old.unlink()
                    except OSError:
                        pass
                if STATE_BACKUP_FILE.exists():
                    try:
                        recovered = json.loads(STATE_BACKUP_FILE.read_text(encoding="utf-8"))
                        if not isinstance(recovered, dict):
                            raise ValueError("state.json.last-good root is not an object")
                        for k, v in defaults.items():
                            if k not in recovered:
                                recovered[k] = v
                        logger.error(f"state.json recovered from {STATE_BACKUP_FILE}")
                        return recovered
                    except (OSError, json.JSONDecodeError, ValueError) as recovery_error:
                        logger.error(f"state.json last-good recovery failed: {recovery_error}")
            except OSError as e:
                # I/O 错误（权限/占用/读盘）：不搬文件，记录后重试
                logger.error(f"state.json read failed (not corrupted): {e}")
        return defaults

    def _save_state(self):
        """原子写入主状态并保留上一份可恢复快照。"""
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.state, indent=2)
        tmp = STATE_FILE.with_name(f"{STATE_FILE.name}.{os.getpid()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, STATE_FILE)
        backup_tmp = STATE_BACKUP_FILE.with_name(f"{STATE_BACKUP_FILE.name}.{os.getpid()}.tmp")
        backup_tmp.write_text(payload, encoding="utf-8")
        os.replace(backup_tmp, STATE_BACKUP_FILE)

    def _append_disabled(self, record: dict):
        """追加禁用渠道记录（防重复：同 id 不重复追加）"""
        self.state.setdefault("disabled_channels", [])
        if not any(r["id"] == record["id"] for r in self.state["disabled_channels"]):
            self.state["disabled_channels"].append(record)

    # ── P1: 渠道性能监控 ──────────────────────────────────────────────────

    def _record_channel_perf(self, channel: dict, healthy: bool) -> bool:
        """每个 NewAPI 测试结果只记录一次，避免轮询重复放大同一数据。"""
        channel_id = channel["id"]
        test_time = channel.get("test_time")
        if not test_time or self._last_channel_tests.get(channel_id) == test_time:
            return False
        self._last_channel_tests[channel_id] = test_time
        self.channel_perf[channel_id].append({
            "time": test_time,
            "response_time": channel.get("response_time") or 0,
            "healthy": healthy,
        })
        return True

    def _get_channel_stats(self, channel_id: int) -> Optional[dict]:
        """获取渠道性能统计"""
        history = self.channel_perf.get(channel_id)
        if not history or len(history) < WEIGHT_ADJUST_WINDOW:
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


    def _error_request_ids(self, channel_id: int, message: str) -> str:
        """Best-effort: 在最近 NewAPI 日志中检索匹配该渠道错误的
        request_id / upstream_request_id，附加到 Telegram 告警。
        任何失败只记日志并返回空串，绝不抛出影响主循环。"""
        try:
            logs = self.newapi.get_logs(20) or []
        except Exception as e:
            logger.warning(f"Request-id lookup failed: {e}")
            return ""
        fragment = (message or "").strip()[:60].lower()
        for log in logs:
            if not isinstance(log, dict):
                continue
            log_channel = log.get("channel_id")
            if log_channel is not None and str(log_channel) != str(channel_id):
                continue
            if fragment and fragment not in str(log.get("content") or "").lower():
                continue
            ids = [str(log[k]) for k in ("request_id", "upstream_request_id") if log.get(k)]
            if ids:
                return " ".join(ids)
        return ""

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
            # 瞬态限流（429/rate limit）：不是渠道故障——不禁用、不累计永久失败
            if _is_transient_rate_limit(test_msg):
                logger.info(
                    f"Channel {channel_id} ({name}) error scan rate-limited, skipped: {test_msg[:100]}"
                )
                continue
            if _is_probe_incompatible(test_msg):
                logger.info(
                    f"Channel {channel_id} ({name}) error scan probe-incompatible, skipped: {test_msg[:100]}"
                )
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
                    self._append_disabled({
                        "id": channel_id,
                        "name": name,
                        "reason": f"error_scan: {matched_keyword} — {test_msg[:80]}",
                        "time": datetime.now().isoformat(),
                    })
                    self.state.setdefault("degraded_channels", {})
                    self.state["degraded_channels"].pop(str(channel_id), None)
                    self._save_state()
                    request_ids = self._error_request_ids(channel_id, test_msg)
                    detail = (
                        f"渠道 <b>{_html_escape(name)}</b> (id: {channel_id}) 已自动禁用\n"
                        f"原因: {_html_escape(matched_keyword)}\n"
                        f"详情: {_html_escape(test_msg[:120])}\n"
                    )
                    if request_ids:
                        detail += f"请求ID: {_html_escape(request_ids)}\n"
                    self.telegram.send_alert(
                        "渠道错误禁用",
                        detail + f"时间: {datetime.now().strftime('%H:%M:%S')}",
                        "warning"
                    )

    # ── P1: 自动降权（渐进式处理） ────────────────────────────────────────

    def degrade_channel_weight(self, channel: dict, reason: str) -> bool:
        """降权渠道（不是直接禁用，而是降低权重）"""
        channel_id = channel["id"]
        name = channel["name"]
        current_weight = channel.get("weight", 0)
        # 自愈节流：距上次降权不足 WEIGHT_DEGRADE_COOLDOWN_MIN 则跳过。
        # 独立于告警冷却 — 告警是否发送不应决定自愈动作是否执行。
        degraded = self.state.get("degraded_channels", {})
        last_degrade = degraded.get(str(channel_id), {}).get("time")
        if last_degrade:
            try:
                if datetime.now() - datetime.fromisoformat(last_degrade) < timedelta(
                    minutes=WEIGHT_DEGRADE_COOLDOWN_MIN
                ):
                    logger.debug(
                        f"Channel {channel_id} ({name}) degrade skipped (cooldown)"
                    )
                    return False
            except (ValueError, TypeError):
                pass

        # 首次降权时记录原始权重（if not present 保证不被后续降权覆盖），
        # 供恢复时还原。否则降权链 10→5→2→1 后禁用会记录 weight=1 丢失原始值。
        self.state.setdefault("weight_history", {})
        if str(channel_id) not in self.state["weight_history"] and current_weight > 0:
            self.state["weight_history"][str(channel_id)] = {
                "weight": current_weight,
                "priority": channel.get("priority", 50),
                "time": datetime.now().isoformat(),
            }

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
                f"渠道 <b>{_html_escape(name)}</b> (id: {channel_id}) 已降权\n"
                f"原因: {_html_escape(reason)}\n"
                f"权重: {current_weight} → {new_weight}\n"
                f"时间: {datetime.now().strftime('%H:%M:%S')}",
                "warning"
            )
            logger.info(f"Channel {channel_id} ({name}) degraded: weight {current_weight} → {new_weight}")
            return True
        return False

    # ── P1: 权重自动调整 ──────────────────────────────────────────────────

    def _auto_adjust_weights(self, channels: List[dict]):
        """按完整的新测试窗口调权；每个窗口最多执行一次动作。"""
        degraded = self.state.get("degraded_channels", {})
        for channel in channels:
            channel_id = channel["id"]
            if channel.get("status") != 1 or channel.get("weight", 0) == 0:
                continue

            stats = self._get_channel_stats(channel_id)
            if not stats:
                continue

            history = self.channel_perf[channel_id]
            current_weight = channel.get("weight", 0)
            cid_str = str(channel_id)
            is_degraded = cid_str in degraded
            try:
                if stats["success_rate"] < WEIGHT_ADJUST_SUCCESS_THRESHOLD:
                    if not is_degraded:
                        self.degrade_channel_weight(
                            channel,
                            f"success_rate={stats['success_rate']:.0%}",
                        )
                elif stats["avg_response_time"] > WEIGHT_ADJUST_SLOW_THRESHOLD:
                    if not is_degraded:
                        self.degrade_channel_weight(
                            channel,
                            f"avg_response_time={stats['avg_response_time']:.0f}ms",
                        )
                elif (
                    is_degraded
                    and stats["success_rate"] >= WEIGHT_BOOST_SUCCESS_THRESHOLD
                    and 0 < stats["avg_response_time"] < 10000
                ):
                    saved = self.state.get("weight_history", {}).get(cid_str, {})
                    original_weight = saved.get(
                        "weight",
                        degraded[cid_str].get("original_weight", current_weight),
                    )
                    if current_weight < original_weight:
                        new_weight = min(original_weight, current_weight + 1)
                        updated = channel.copy()
                        updated["weight"] = new_weight
                        if self.newapi.update_channel(updated):
                            channel["weight"] = new_weight
                            logger.info(
                                f"Channel {channel_id} degraded-recovery: weight "
                                f"{current_weight} → {new_weight} (target={original_weight})"
                            )
                    else:
                        del self.state["degraded_channels"][cid_str]
                        self._save_state()
                        logger.info(f"Channel {channel_id} fully recovered, cleared degraded record")
            finally:
                history.clear()

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
            self._append_disabled({
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
                    f"渠道 <b>{_html_escape(name)}</b> (id: {channel_id}) 已自动禁用\n"
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
                f"渠道 <b>{_html_escape(name)}</b> (id: {channel_id}) 已自动启用\n"
                f"时间: {datetime.now().strftime('%H:%M:%S')}",
                "success"
            )
            return True
        return False

    def check_and_enable_recovered_channels(self):
        """检查已禁用渠道是否稳定恢复，再启用并加入聚合池。"""
        tested = 0
        for record in self.state["disabled_channels"][:]:
            if tested >= RECOVERY_BATCH_SIZE:
                break
            if record.get("manual"):
                continue

            channel_id = record["id"]
            name = record["name"]
            failures = record.get("recovery_failures", 0)
            last_attempt = record.get("last_recovery_attempt")
            cooldown_from = last_attempt or record.get("time")
            backoff_min = RECOVERY_COOLDOWN_MIN
            if last_attempt and failures > 0:
                backoff_min = max(
                    RECOVERY_COOLDOWN_MIN,
                    min(RECOVERY_BACKOFF_BASE * (2 ** (failures - 1)), RECOVERY_BACKOFF_MAX),
                )
            if cooldown_from:
                try:
                    if datetime.now() - datetime.fromisoformat(cooldown_from) < timedelta(minutes=backoff_min):
                        continue
                except (ValueError, TypeError):
                    pass

            tested += 1
            record["last_recovery_attempt"] = datetime.now().isoformat()
            current = self.newapi.get_channel(channel_id)
            already_enabled = bool(current and current.get("status") == 1)

            stable_count = 0
            probe_incompatible_count = 0
            test_msg = "无响应"
            for attempt in range(RECOVERY_TEST_COUNT):
                test_ok, test_msg = self.newapi.test_channel(channel_id)
                stable_count += int(test_ok)
                probe_incompatible_count += int(not test_ok and _is_probe_incompatible(test_msg))
                if attempt + 1 < RECOVERY_TEST_COUNT:
                    time.sleep(1)

            if probe_incompatible_count == RECOVERY_TEST_COUNT:
                self._save_state()
                logger.info(
                    f"Channel {channel_id} ({name}) recovery probes all incompatible; "
                    "status left unchanged"
                )
                continue
            if stable_count >= RECOVERY_TEST_PASS_MIN:
                enabled = already_enabled or self.newapi.enable_channel(channel_id)
                if enabled and self._auto_join_pool(channel_id, name):
                    self.state["disabled_channels"].remove(record)
                    self._save_state()
                    logger.info(
                        f"Channel {channel_id} ({name}) recovered "
                        f"({stable_count}/{RECOVERY_TEST_COUNT} checks passed)"
                    )
                    continue
                test_msg = "聚合池加入失败" if enabled else "渠道启用失败"
                if enabled and self.newapi.disable_channel(channel_id):
                    logger.warning(f"Channel {channel_id} ({name}) recovery commit failed; disabled again")
            elif already_enabled:
                if self.newapi.disable_channel(channel_id):
                    logger.warning(f"Channel {channel_id} ({name}) auto-enabled before stable; disabled again")
                else:
                    logger.error(f"Channel {channel_id} ({name}) failed recovery and could not be re-disabled")

            record["recovery_failures"] = failures + 1
            self._save_state()
            logger.debug(
                f"Channel {channel_id} ({name}) recovery failed: {test_msg}; "
                f"backoff #{failures + 1}"
            )

    def _auto_join_pool(self, channel_id: int, name: str) -> bool:
        """恢复渠道权重并登记稳定性监控；PUT 会同步 NewAPI abilities。

        priority 是人工路由策略，不属于健康状态；恢复时只调整 weight。
        """
        try:
            channel = self.newapi.get_channel(channel_id)
            if not channel:
                return False
            # 渠道声明的模型名即 NewAPI 公开路由名（含 model_mapping 左侧别名），
            # 是加入聚合池的 SSOT。/api/models 返回上游发现模型，不保证包含这些别名
            # （如 opencode-go、deepseek-official-v4-flash），不得用它过滤。
            pool_models = [m.strip() for m in channel.get("models", "").split(",") if m.strip()]
            if not pool_models:
                logger.info(f"Channel {channel_id} ({name}) has no models, skipping join")
                return False

            history = self.state.setdefault("weight_history", {}).get(str(channel_id))
            if history and history.get("weight", 0) > 0:
                desired_weight = history["weight"]
                logger.info(f"Channel {channel_id} restoring weight from history: {desired_weight}")
            else:
                desired_weight = 5

            desired_weight = self._balance_pool_weights(pool_models, channel_id, desired_weight)
            current_weight = channel.get("weight", 0)
            current_priority = channel.get("priority", 50)
            if current_weight != desired_weight:
                updated = channel.copy()
                updated["weight"] = desired_weight
                if not self.newapi.update_channel(updated):
                    logger.error(f"Channel {channel_id} update_channel failed during auto_join_pool")
                    return False

            self.state.setdefault("degraded_channels", {}).pop(str(channel_id), None)
            self.state.setdefault("joined_channels", {})[str(channel_id)] = {
                "time": datetime.now().isoformat(),
                "models": pool_models,
                "weight": desired_weight,
                "priority": current_priority,
                "stability_checks": 0,
                "stability_fails": 0,
            }
            self._save_state()
            logger.info(
                f"Channel {channel_id} ({name}) joined pool: weight={desired_weight}, "
                f"priority={current_priority}, models={pool_models}"
            )
            self.telegram.send_alert(
                "渠道加入聚合池",
                f"渠道 <b>{_html_escape(name)}</b> (id: {channel_id}) 已恢复并加入聚合池\n"
                f"模型: {_html_escape(', '.join(pool_models))}\n"
                f"权重: {desired_weight}, 优先级: {current_priority}\n"
                f"时间: {datetime.now().strftime('%H:%M:%S')}",
                "success",
            )
            return True
        except Exception as e:
            logger.error(f"Auto join pool failed for channel {channel_id}: {e}")
            return False

    def _balance_pool_weights(self, joined_models: List[str], new_channel_id: int, new_weight: int) -> int:
        """大池中仅限制恢复渠道权重，不改写健康同伴的用户配置。"""
        try:
            channels = self.newapi.get_channels()
            balanced_weight = new_weight
            for model in joined_models:
                peers = [
                    ch for ch in channels
                    if ch.get("status") == 1
                    and ch.get("weight", 0) > 0
                    and ch.get("id") != new_channel_id
                    and model in [m.strip() for m in ch.get("models", "").split(",")]
                ]
                if len(peers) >= 5:
                    peer_average = max(MIN_WEIGHT, sum(ch["weight"] for ch in peers) // len(peers))
                    balanced_weight = min(balanced_weight, peer_average)
            if balanced_weight < new_weight:
                logger.info(
                    f"Balanced recovered channel {new_channel_id}: weight {new_weight} → {balanced_weight}"
                )
            return balanced_weight
        except Exception as e:
            logger.error(f"Balance pool weights failed: {e}")
            return new_weight

    def _check_joined_channels_stability(self):
        """P0: 回滚机制 — 定期检查 joined_channels 稳定性，不稳定时自动回滚."""
        if "joined_channels" not in self.state or not self.state["joined_channels"]:
            return
        self._stability_count += 1
        if self._stability_count % JOIN_STABILITY_CHECK_INTERVAL != 0:
            return
        for channel_id_str, join_info in list(self.state["joined_channels"].items()):
            channel_id = int(channel_id_str)
            try:
                join_time = datetime.fromisoformat(join_info["time"])
            except (ValueError, TypeError, KeyError):
                del self.state["joined_channels"][channel_id_str]
                self._save_state()
                continue
            if datetime.now() - join_time > timedelta(minutes=JOIN_STABILITY_WINDOW_MIN):
                del self.state["joined_channels"][channel_id_str]
                self._save_state()
                continue

            join_info["stability_checks"] = join_info.get("stability_checks", 0) + 1
            test_ok, test_msg = self.newapi.test_channel(channel_id)
            if not test_ok and _is_probe_incompatible(test_msg):
                logger.info(
                    f"Channel {channel_id} stability probe-incompatible, skipped: {test_msg[:100]}"
                )
                self._save_state()
                continue
            if not test_ok:
                join_info["stability_fails"] = join_info.get("stability_fails", 0) + 1
                logger.warning(f"Channel {channel_id} stability check failed: {test_msg}")
                if join_info["stability_fails"] >= 2:
                    channel = self.newapi.get_channel(channel_id)
                    if channel and self.newapi.disable_channel(channel_id):
                        self._append_disabled({
                            "id": channel_id,
                            "name": channel.get("name", str(channel_id)),
                            "reason": f"stability_rollback: {test_msg[:80]}",
                            "time": datetime.now().isoformat(),
                            "manual": False,
                        })
                        self.telegram.send_alert(
                            "渠道回滚",
                            f"渠道 <b>{_html_escape(channel.get('name', channel_id))}</b> (id: {channel_id}) 加入后不稳定，已禁用\n"
                            f"失败次数: {join_info['stability_fails']}\n"
                            f"原因: {_html_escape(test_msg)}\n"
                            f"恢复后将自动重新启用\n"
                            f"时间: {datetime.now().strftime('%H:%M:%S')}",
                            "warning",
                        )
                        del self.state["joined_channels"][channel_id_str]
            else:
                if join_info.get("stability_fails", 0) > 0:
                    join_info["stability_fails"] = 0
                    logger.info(f"Channel {channel_id} stability check passed, fails reset")
            self._save_state()


    def check_omp_roles_health(self):
        """主动检测 OMP 角色指向的 provider 端点是否存活（只报警，不自动切换）

        每 OMP_ROLE_CHECK_INTERVAL 周期运行一次。读取 config.yml 的 modelRoles 和
        models.yml 的 provider baseUrl，对本地代理端点做 HTTP 探测。
        尊重用户意图：发现死端点只发 Telegram 报警，不擅自修改 modelRoles。
        """
        self._omp_check_count += 1
        if self._omp_check_count % OMP_ROLE_CHECK_INTERVAL != 0:
            return

        try:
            config_path = Path.home() / ".omp" / "agent" / "config.yml"
            models_path = Path.home() / ".omp" / "agent" / "models.yml"
            if not config_path.exists() or not models_path.exists():
                return

            # 解析 models.yml 的 provider → baseUrl（简单行解析，避免依赖 yaml 模块）
            provider_base: Dict[str, str] = {}
            current_provider = None
            for line in models_path.read_text(encoding="utf-8").split("\n"):
                # provider 键：2 空格缩进，以冒号结尾
                m_prov = re.match(r'^  ([\w\-]+):\s*$', line)
                if m_prov:
                    current_provider = m_prov.group(1)
                    continue
                m_base = re.match(r'^    baseUrl:\s*(\S+)', line)
                if m_base and current_provider:
                    provider_base[current_provider] = m_base.group(1).strip()

            # 解析 config.yml 的 modelRoles：role → provider/model
            dead_roles = []
            in_roles = False
            for line in config_path.read_text(encoding="utf-8").split("\n"):
                if line.startswith("modelRoles:"):
                    in_roles = True
                    continue
                if in_roles:
                    m_role = re.match(r'^  ([\w]+):\s*(.+)$', line)
                    if not m_role:
                        if line and not line.startswith(" "):
                            in_roles = False  # 离开 modelRoles 块
                        continue
                    role, value = m_role.group(1), m_role.group(2).strip()
                    if value.startswith('"'):
                        value = value.strip('"')
                    if value.startswith("@") or "/" not in value:
                        continue  # 引用角色或无 provider，跳过
                    provider = value.split("/", 1)[0]
                    base = provider_base.get(provider)
                    if not base:
                        continue
                    # 仅探测本机代理端点（本地代理均只绑定 127.0.0.1）
                    if not any(host in base for host in ("127.0.0.1", "localhost")):
                        continue
                    if not self._probe_endpoint(base):
                        dead_roles.append(f"{role}: {value} ({base})")

            if dead_roles and self.telegram:
                self.telegram.send_alert(
                    "OMP 角色端点故障",
                    "以下 OMP 角色指向的本地代理端点无响应:\n  "
                    + "\n  ".join(_html_escape(d) for d in dead_roles)
                    + "\n\n请检查对应代理或手动切换角色",
                    "warning"
                )
        except Exception as e:
            logger.error(f"check_omp_roles_health failed: {e}")

    @staticmethod
    def _probe_endpoint(base_url: str) -> bool:
        """探测端点存活（短超时）

        - 路径感知：base 已含 /v1 时只拼 /models，否则拼 /v1/models（避免 /v1/v1/models 404）
        - 本地代理需鉴权：按端口带对应 Bearer key（agentrouter 8788 / codebuddy 8787）
        - 语义：任何 HTTP 响应（含 401/403）都算存活，只有连接失败/超时才算死
        """
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            models_path = base + "/models"
        else:
            models_path = base + "/v1/models"

        # 本地代理端口 → 探针 key
        probe_key = "any"
        for port, key in ((8788, AGENTROUTER_PROXY_KEY), (8787, CODEBUDDY_API_KEY)):
            if f":{port}" in models_path:
                probe_key = key
                break

        try:
            req = urllib.request.Request(models_path, headers={"Authorization": f"Bearer {probe_key}"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status < 500  # 2xx/3xx/4xx 都算存活
        except urllib.error.HTTPError as error:
            # 4xx 是客户端拒绝（服务存活）；5xx 是服务端故障，不算存活
            return error.code < 500
        except Exception:
            return False

    # ── 自循环维护（让系统无需人工干预持续运转） ──────────────────────────

    def full_health_scan(self):
        """全量健康扫描：定期轮转探测启用渠道，捕获渐进退化

        补充 NewAPI 内置 30min 自动测试。每 FULL_SCAN_INTERVAL 周期触发，
        每次测 FULL_SCAN_BATCH_SIZE 个渠道并轮转偏移。
        """
        self._full_scan_count += 1
        if self._full_scan_count % FULL_SCAN_INTERVAL != 0:
            return

        channels = self.newapi.get_channels()
        enabled = [c for c in channels if c.get("status") == 1 and c.get("weight", 0) > 0]
        n = len(enabled)
        if n == 0:
            return

        # 轮转批次：每次触发测一批，偏移推进，多次触发覆盖全部渠道
        offset = self._full_scan_offset % n
        batch = (enabled[offset:] + enabled[:offset])[:FULL_SCAN_BATCH_SIZE]
        self._full_scan_offset = (offset + FULL_SCAN_BATCH_SIZE) % n

        scanned = 0
        degraded = 0
        for channel in batch:
            channel_id = channel["id"]
            test_ok, test_msg = self.newapi.test_channel(channel_id)
            scanned += 1
            if test_ok:
                self._full_scan_failures.pop(channel_id, None)
                continue
            # 限流与探针形态不相容都不提供渠道健康结论，不累计破坏性失败。
            if _is_transient_rate_limit(test_msg):
                logger.info(
                    f"Full scan: channel {channel_id} rate-limited, skipped (transient): {test_msg[:100]}"
                )
                continue
            if _is_probe_incompatible(test_msg):
                logger.info(
                    f"Full scan: channel {channel_id} probe-incompatible, skipped: {test_msg[:100]}"
                )
                continue
            msg_lower = test_msg.lower()
            # 硬错误（402/401）直接禁用
            if any(kw.lower() in msg_lower for kw in ERROR_DISABLE_KEYWORDS):
                self._full_scan_failures.pop(channel_id, None)
                self.state.setdefault("weight_history", {})
                if str(channel_id) not in self.state["weight_history"]:
                    self.state["weight_history"][str(channel_id)] = {
                        "weight": channel.get("weight", 5),
                        "priority": channel.get("priority", 50),
                        "time": datetime.now().isoformat(),
                    }
                if self.newapi.disable_channel(channel_id):
                    self._append_disabled({
                        "id": channel_id, "name": channel["name"],
                        "reason": f"full_scan: {test_msg[:80]}",
                        "time": datetime.now().isoformat(), "manual": False,
                    })
                    self._save_state()
                    logger.warning(f"Full scan disabled channel {channel_id}: {test_msg[:80]}")
                continue

            failures = self._full_scan_failures.get(channel_id, 0) + 1
            self._full_scan_failures[channel_id] = failures
            if failures < CHANNEL_FAIL_THRESHOLD:
                logger.warning(
                    f"Full scan soft failure {failures}/{CHANNEL_FAIL_THRESHOLD} "
                    f"for channel {channel_id}: {test_msg[:80]}"
                )
                continue

            # 软错误仅在连续失败达到阈值后降权。
            if self.degrade_channel_weight(channel, f"full_scan: {test_msg[:60]}"):
                degraded += 1
                self._full_scan_failures.pop(channel_id, None)
        logger.info(f"Full health scan batch: {scanned} channels, {degraded} degraded (offset={offset}/{n})")

    def periodic_ability_fix(self):
        """周期性修复 NewAPI 路由投影并排除无意义的 402 池内重试。"""
        self._ability_fix_count += 1
        if self._ability_fix_count % ABILITY_FIX_INTERVAL != 0:
            return
        if not self.newapi.exclude_retry_status_code(402):
            logger.warning("Periodic 402 retry policy check failed")
        if self.newapi.fix_channel_abilities():
            logger.info("Periodic ability fix completed")
        else:
            logger.warning("Periodic ability fix failed")

    def cleanup_stale_state(self):
        """清理无主状态；仍被 Guardian 管理的禁用/降权记录必须保留。"""
        self._cleanup_count += 1
        if self._cleanup_count % STATE_CLEANUP_INTERVAL != 0:
            return

        cutoff = datetime.now() - timedelta(hours=STATE_MAX_AGE_HOURS)
        cleaned = 0
        channels = {str(c["id"]): c for c in self.newapi.get_channels()}

        disabled = self.state.get("disabled_channels", [])
        for record in disabled[:]:
            cid = str(record["id"])
            if cid not in channels:
                disabled.remove(record)
                self.state.get("weight_history", {}).pop(cid, None)
                self.state.get("degraded_channels", {}).pop(cid, None)
                self.state.get("joined_channels", {}).pop(cid, None)
                cleaned += 1

        managed_ids = {str(record["id"]) for record in disabled}
        managed_ids.update(self.state.get("degraded_channels", {}))
        managed_ids.update(self.state.get("joined_channels", {}))
        for cid, entry in list(self.state.get("weight_history", {}).items()):
            if cid in managed_ids:
                continue
            try:
                stale = datetime.fromisoformat(entry.get("time", "")) < cutoff
            except (ValueError, TypeError):
                stale = True
            if stale:
                del self.state["weight_history"][cid]
                cleaned += 1

        for cid in list(self.state.get("degraded_channels", {})):
            if cid not in channels:
                del self.state["degraded_channels"][cid]
                cleaned += 1

        for name, timestamp in list(self.state.get("restarted_proxies", {}).items()):
            try:
                stale = datetime.fromisoformat(timestamp) < cutoff
            except (ValueError, TypeError):
                stale = True
            if stale:
                del self.state["restarted_proxies"][name]
                self.state.setdefault("restart_counts", {}).pop(name, None)
                self.state.setdefault("restart_alerted", {}).pop(name, None)
                cleaned += 1

        if cleaned:
            self._save_state()
            logger.info(f"State cleanup: removed {cleaned} stale entries")

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

    def restart_local_proxy(self, name: str, port: int) -> bool:
        info = LOCAL_PROXIES.get(name)
        if not info:
            logger.error(f"Unknown proxy: {name}")
            return False

        restart_count = self.state["restart_counts"].get(name, 0)
        if restart_count >= 3:
            # 断路器打开：告警只发一次，避免每 15s 周期重复刷屏
            alerted = self.state.setdefault("restart_alerted", {}).get(name)
            if not alerted:
                self.state["restart_alerted"][name] = True
                self._save_state()
                self.telegram.send_alert(
                    "本地代理重启失败",
                    f"代理 <b>{_html_escape(name)}</b> (端口 {port}) 已重启 {restart_count} 次仍失败\n"
                    f"请手动检查",
                    "error"
                )
            else:
                logger.warning(f"代理 {name} 断路器打开（已告警，不再重复）")
            return False

        try:
            # 本地代理均以 python.exe 启动；按脚本名识别旧进程。
            proc_name = "python.exe"
            script_pattern = re.escape(info["script"])
            ps_cmd = f'Get-CimInstance Win32_Process -Filter "Name=\'{proc_name}\'" | Where-Object {{ $_.CommandLine -match \'{script_pattern}\' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}'
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                shell=False, capture_output=True, timeout=10
            )

            time.sleep(2)


            script_path = Path(info["dir"]) / info["script"]
            if not script_path.exists():
                logger.error(f"Script not found: {script_path}")
                return False

            env = None
            if name == "agentrouter":
                cmd = [
                    "C:/Users/zhugu/scoop/apps/python313/current/python.exe",
                    str(script_path),
                    "--host", LOCAL_PROXY_BIND_HOST,
                    "--port", str(port),
                    "--log", "proxy.log"
                ]
                env = {**os.environ, "AGENTROUTER_PROXY_KEY": AGENTROUTER_PROXY_KEY}
            elif name == "codebuddy":
                cmd = [
                    "C:/Users/zhugu/scoop/apps/python313/current/python.exe",
                    str(script_path),
                    "--host", LOCAL_PROXY_BIND_HOST,
                    "--log", "converter.log"
                ]
                env = {**os.environ, "CODEBUDDY2OPENAI_KEY": CODEBUDDY_API_KEY}
            else:
                logger.error(f"Unknown proxy type: {name}")
                return False

            subprocess.Popen(
                cmd,
                cwd=info["dir"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )

            # 重启后验证：等待进程启动并探测端口（最多 10s）。
            # 用独立 TCP 探测而非 check_local_proxy 恒真桩——否则新进程秒退、
            # 端口未绑定也会误报"已重启并验证正常"、错误清零计数
            verified = False
            for attempt in range(5):
                reachable, _ = self.health.check_local_endpoint(port, name)
                if reachable:
                    verified = True
                    break
                if attempt + 1 < 5:
                    time.sleep(2)

            if verified:
                self.state["restart_counts"][name] = 0
                self.state["restarted_proxies"][name] = datetime.now().isoformat()
                self.state.setdefault("restart_alerted", {}).pop(name, None)
                self._save_state()
                self.telegram.send_alert(
                    "本地代理重启",
                    f"代理 <b>{_html_escape(name)}</b> (端口 {port}) 已重启并验证端口可达\n"
                    f"次数: {restart_count + 1}\n"
                    f"时间: {datetime.now().strftime('%H:%M:%S')}",
                    "restart",
                )
                return True

            self.state["restart_counts"][name] = restart_count + 1
            self.state["restarted_proxies"][name] = datetime.now().isoformat()
            self._save_state()
            self.telegram.send_alert(
                "本地代理重启未验证",
                f"代理 <b>{_html_escape(name)}</b> (端口 {port}) 启动后端口仍未响应\n"
                f"次数: {restart_count + 1}，可能启动失败\n"
                f"时间: {datetime.now().strftime('%H:%M:%S')}",
                "warning",
            )
            return False
        except Exception as e:
            logger.error(f"Restart {name} failed: {e}")
            return False

    def restart_newapi_container(self) -> bool:
        """本地 NewAPI 的自动重启已禁用（远端 VPS 实例已删除，原 SSH 重启路径已移除）。

        健康检查失败时只告警，请使用本机启动脚本处理。
        """
        logger.error("NewAPI health failure: automatic restart is disabled for the local service")
        self.telegram.send_alert(
            "NewAPI 健康检查失败",
            "本地 NewAPI 自动重启已禁用；请使用本机启动脚本处理。",
            "error",
        )
        return False

    def export_metrics(self, channels: List[dict], error_rate: float, remaining: int):
        """P2: 导出 JSON 指标（含渠道生命周期状态机视图）"""
        try:
            lifecycle = self.derive_channel_lifecycle(channels)
            metrics = {
                "timestamp": datetime.now().isoformat(),
                "channels": {
                    "total": len(channels),
                    "healthy": sum(1 for c in channels if c.get("status") == 1),
                    "disabled": sum(1 for c in channels if c.get("status") != 1),
                },
                "lifecycle": {state: len(items) for state, items in lifecycle.items()},
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

    def derive_channel_lifecycle(self, channels: List[dict]) -> Dict[str, List[dict]]:
        """派生每个渠道的统一生命周期状态（显式状态机视图）

        现有自愈逻辑把状态分散在 status/weight/degraded_channels/
        joined_channels/disabled_channels 多处。此方法派生统一视图，
        让渠道生命周期可观测、可审计。

        生命周期状态:
          active         — 正常服务（status=1, weight>0, 无降权/监控标记）
          degraded       — 降权中（性能差，权重已降，渐进恢复中）
          monitoring     — 稳定性监控中（刚恢复加入，观察期内）
          disabled_auto  — 自动禁用（故障，将自动恢复）
          disabled_manual— 手动禁用（用户操作，不自动恢复）
          disabled_orphan— 孤儿禁用（Guardian 外禁用，如 NewAPI 自身）
        """
        degraded_ids = set(self.state.get("degraded_channels", {}).keys())
        monitoring_ids = set(self.state.get("joined_channels", {}).keys())
        disabled_map = {str(r["id"]): r for r in self.state.get("disabled_channels", [])}

        lifecycle: Dict[str, List[dict]] = {
            "active": [], "degraded": [], "monitoring": [],
            "disabled_auto": [], "disabled_manual": [], "disabled_orphan": [],
        }

        for ch in channels:
            cid = str(ch["id"])
            info = {"id": ch["id"], "name": ch["name"], "weight": ch.get("weight", 0)}

            if ch.get("status") == 1:
                if cid in degraded_ids:
                    lifecycle["degraded"].append(info)
                elif cid in monitoring_ids:
                    lifecycle["monitoring"].append(info)
                elif ch.get("weight", 0) > 0:
                    lifecycle["active"].append(info)
                else:
                    # status=1 但 weight=0：僵尸态（不应出现，回滚已修复）
                    lifecycle["disabled_orphan"].append(info)
            else:
                if cid in disabled_map:
                    rec = disabled_map[cid]
                    key = "disabled_manual" if rec.get("manual") else "disabled_auto"
                    info["reason"] = rec.get("reason", "")
                    lifecycle[key].append(info)
                else:
                    lifecycle["disabled_orphan"].append(info)

        return lifecycle

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
        """检查是否应告警（按级别冷却）。放行时记录时间戳，否则冷却永不生效。"""
        now = datetime.now()
        last = self.last_alerts.get(alert_type)
        cooldown = self.alert_cooldowns.get(level, timedelta(minutes=5))
        if last is not None and now - last <= cooldown:
            return False
        self.last_alerts[alert_type] = now
        return True

    def send_daily_report(self, stats: dict) -> bool:
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
        return bool(self.telegram.send(text))

# ═══════════════════════════════════════════════════════════════════════════
# 主循环
# ═══════════════════════════════════════════════════════════════════════════

class Guardian:
    def __init__(self):
        required = {
            "newapi_token": NEWAPI_TOKEN,
            "telegram_token": TELEGRAM_TOKEN,
            "telegram_chat_id": TELEGRAM_CHAT_ID,
            "codebuddy_api_key": CODEBUDDY_API_KEY,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"Missing Guardian secrets: {', '.join(missing)} ({SECRETS_FILE})")
        self.telegram = TelegramBot(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_PROXY)
        self.newapi = NewAPIClient(NEWAPI_BASE, NEWAPI_TOKEN, NEWAPI_USER)
        self.health = HealthChecker(self.newapi)
        self.autofix = AutoFixEngine(self.newapi, self.telegram, self.health)
        self.alerts = AlertManager(self.telegram)
        self.running = True

    def get_subagent_status(self) -> List[dict]:
        """Return recent OMP subagent role, model, and lifecycle snapshots."""
        root = Path.home() / ".omp" / "agent" / "sessions"
        if not root.exists():
            return []
        now = time.time()
        result = []
        for path in root.glob("*/*/*.jsonl"):
            try:
                age_sec = int(max(0, now - path.stat().st_mtime))
                if age_sec > 7200:
                    continue
                model = "unknown"
                completed = False
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("type") == "model_change":
                        model = event.get("model") or model
                    elif event.get("type") == "session_exit" or (
                        event.get("type") == "custom"
                        and (
                            event.get("customType") == "session_exit"
                            or (
                                event.get("customType") == "tool_execution_start"
                                and event.get("data", {}).get("toolName") == "yield"
                            )
                        )
                    ):
                        completed = True
                status = "completed" if completed else ("stalled" if age_sec > 300 else "running")
                result.append({
                    "name": path.stem,
                    "model": model,
                    "status": status,
                    "age_sec": age_sec,
                })
            except OSError:
                continue
        return sorted(result, key=lambda item: item["age_sec"])[:20]

    def run(self):
        logger.info("NewAPI Guardian 启动")
        if not self.newapi.exclude_retry_status_code(402):
            logger.warning("Startup 402 retry policy check failed")
        self.telegram.send_alert(
            "Guardian 启动",
            "NewAPI 自愈系统已启动\n"
            f"监控间隔: {HEALTH_CHECK_INTERVAL} 秒\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"发送 /help 查看可用命令",
            "info"
        )

        while self.running:
            cycle_start = time.time()
            try:
                # 心跳：外部 watchdog 监视此文件新鲜度，超时判定 Guardian 卡死。
                # 独立 try：写失败只影响存活信号，不阻断本轮自愈
                try:
                    hb_tmp = HEARTBEAT_FILE.with_suffix(".json.tmp")
                    hb_tmp.write_text(json.dumps({
                        "ts": datetime.now().isoformat(),
                        "pid": os.getpid(),
                    }), encoding="utf-8")
                    os.replace(hb_tmp, HEARTBEAT_FILE)
                except OSError as hb_err:
                    logger.error(f"Heartbeat write failed: {hb_err}")
                self.telegram.process_commands(self)
                self._check_cycle()
            except KeyboardInterrupt:
                logger.info("Guardian 停止")
                break
            except Exception as e:
                logger.error(f"Check cycle error: {e}")

            # 自身健康监控：周期耗时超阈值则告警（说明某步骤阻塞）
            cycle_ms = (time.time() - cycle_start) * 1000
            if cycle_ms > CYCLE_TIME_WARN_MS:
                if self.alerts.should_alert("cycle_slow", "warning"):
                    self.telegram.send_alert(
                        "Guardian 周期过慢",
                        f"检查周期耗时 {cycle_ms/1000:.1f}s（阈值 {CYCLE_TIME_WARN_MS/1000:.0f}s）\n"
                        f"某步骤可能阻塞，请检查日志",
                        "warning"
                    )
                logger.warning(f"Cycle took {cycle_ms/1000:.1f}s (threshold {CYCLE_TIME_WARN_MS/1000:.0f}s)")

            time.sleep(HEALTH_CHECK_INTERVAL)

    def _check_cycle(self):
        # 单轮预算：故障时避免无限拉长周期。高优先级（1/2/6 代理重启）必须做，
        # 其余步骤超预算则跳过并记日志，下轮再补
        self._cycle_deadline = time.monotonic() + CYCLE_BUDGET_SEC

        # 1. NewAPI 健康：连续 NEWAPI_FAIL_THRESHOLD 次失败才触发破坏性重启。
        newapi_ok, newapi_msg = self.health.check_newapi()
        if newapi_ok:
            state_changed = False
            if self.autofix.state.get("newapi_fail_streak", 0):
                self.autofix.state["newapi_fail_streak"] = 0
                state_changed = True
            if self.autofix.state.pop("newapi_outage_alerted", None) is not None:
                state_changed = True
            if state_changed:
                self.autofix._save_state()
        else:
            streak = self.autofix.state.get("newapi_fail_streak", 0) + 1
            self.autofix.state["newapi_fail_streak"] = streak
            self.autofix._save_state()
            if (
                streak >= NEWAPI_FAIL_THRESHOLD
                and not self.autofix.state.get("newapi_outage_alerted")
                and self.alerts.should_alert("newapi_health", "error")
            ):
                # 按故障段只告警一次；标记持久化，Guardian 重启或通用冷却到期也不重发。
                self.autofix.state["newapi_outage_alerted"] = True
                self.autofix._save_state()
                self.autofix.restart_newapi_container()

        # 2. 本地代理健康：只有端口无响应才重启；进程存活但上游异常只告警。
        for name, info in LOCAL_PROXIES.items():
            ok, msg, alive = self.health.check_local_proxy(info["port"], name)
            proxy_fail_streaks = self.autofix.state.setdefault("proxy_fail_streaks", {})
            if ok or alive:
                if proxy_fail_streaks.get(name, 0):
                    proxy_fail_streaks[name] = 0
                    self.autofix._save_state()
                if ok:
                    # 推理恢复：清除推理告警标记，下次故障段重新告警
                    if self.autofix.state.setdefault("inference_alerted", {}).pop(name, None):
                        self.autofix._save_state()
                    if self.autofix.state.get("restart_counts", {}).get(name, 0) > 0:
                        self.autofix.state["restart_counts"][name] = 0
                        self.autofix.state.setdefault("restart_alerted", {}).pop(name, None)
                        self.autofix._save_state()
                elif (
                    not self.autofix.state.setdefault("inference_alerted", {}).get(name)
                    and self.alerts.should_alert(f"proxy_{name}", "error")
                ):
                    # 连续推理异常每故障段只告警一次，恢复后重新武装（防上游持续慢时刷屏）
                    self.autofix.state["inference_alerted"][name] = True
                    self.autofix._save_state()
                    self.telegram.send_alert("本地代理推理异常", _html_escape(msg), "error")
            else:
                streak = proxy_fail_streaks.get(name, 0) + 1
                proxy_fail_streaks[name] = streak
                self.autofix._save_state()
                if streak < 3:
                    logger.warning(f"代理 {name} 端口探测失败 {streak}/3，等待确认: {msg}")
                    continue
                breaker_open = self.autofix.state.get("restart_counts", {}).get(name, 0) >= 3
                restarted = self.autofix.restart_local_proxy(name, info["port"])
                proxy_fail_streaks[name] = 0
                self.autofix._save_state()
                if (
                    not restarted
                    and not breaker_open
                    and self.alerts.should_alert(f"proxy_{name}", "error")
                ):
                    self.telegram.send_alert("本地代理故障", _html_escape(msg), "error")

        # NewAPI 已判定不可达：保留本地代理检查，但不再向同一故障端点扇出渠道、
        # 日志、余额和 abilities 请求。下轮健康检查恢复后自动继续完整闭环。
        if not newapi_ok:
            logger.warning(f"NewAPI unavailable; skipped dependent work: {newapi_msg}")
            return
        # 2.5 P0: 错误渠道扫描（402/401/502 等瞬间返回的错误）
        if self._budget_left("error scan"):
            self.autofix.scan_error_channels()

        # 3. P1: 权重自动调整（根据性能历史）
        if self._budget_left("weight adjust"):
            # 重新获取渠道列表：步骤 2/2.5 可能已禁用/降权某些渠道，过期列表会错误处理它们
            channels = self.newapi.get_channels()
            self.autofix._auto_adjust_weights(channels)

        # 4. P0: 检查已禁用渠道是否恢复（自动启用 + 防抖动）
        if self._budget_left("recovery check"):
            self.autofix.check_and_enable_recovered_channels()

        # 5. P0: 检查已加入渠道的稳定性（回滚机制）— 安全关键，不参与预算跳过：
        # 预算风暴时仍须回滚不稳定渠道，防止其继续服务
        self.autofix._check_joined_channels_stability()

        # 5.5 OMP 角色端点主动检测（只报警，不自动切换）
        if self._budget_left("OMP role check"):
            self.autofix.check_omp_roles_health()




        # 7. 错误率（get_logs 网络调用，超预算跳过并给默认值）
        if self._budget_left("error rate check"):
            ok, rate, errors, total = self.health.check_error_rate()
            if not ok:
                if self.alerts.should_alert("error_rate", "warning"):
                    self.telegram.send_alert(
                        "错误率超标",
                        f"错误率: {rate:.1%} ({errors}/{total})\n"
                        f"阈值: {ERROR_RATE_THRESHOLD:.0%}",
                        "warning"
                    )
        else:
            rate, remaining = 0.0, -1  # 默认值，metrics 不因跳过而 NameError

        # 8. 余额 + P2: 趋势分析（get_user_info 网络调用，预算守卫）
        if self._budget_left("balance check"):
            ok, remaining, quota = self.health.check_balance()
            if remaining >= 0:  # -1 表示 API 失败，不记录不告警
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
        else:
            remaining = -1

        # 9. P2: 导出指标
        if self._budget_left("metrics export"):
            self.autofix.export_metrics(channels, rate, remaining)

        # 10. 自循环维护（无需人工干预持续运转）
        if self._budget_left("full health scan"):
            self.autofix.full_health_scan()
        if self._budget_left("ability fix"):
            self.autofix.periodic_ability_fix()
        if self._budget_left("state cleanup"):
            self.autofix.cleanup_stale_state()

        # 11. 每日报告
        if self._budget_left("daily report"):
            self._maybe_daily_report()

    def _budget_left(self, step: str) -> bool:
        """预算还剩时间？超预算则跳过该低优先级步骤并记日志"""
        left = self._cycle_deadline - time.monotonic()
        if left <= 0:
            logger.warning(f"Cycle budget exceeded, skipping {step}")
            return False
        return True

    def _maybe_daily_report(self, force: bool = False) -> bool:
        last_report = self.autofix.state.get("last_daily_report")
        today = datetime.now().strftime("%Y-%m-%d")

        if force or last_report != today:
            channels = self.newapi.get_channels()
            healthy = sum(1 for c in channels if c.get("status") == 1)
            disabled = len(self.autofix.state["disabled_channels"])
            restarts = len(self.autofix.state["restarted_proxies"])
            _, remaining, quota = self.health.check_balance()
            ok, rate, _, _ = self.health.check_error_rate()

            sent = self.alerts.send_daily_report({
                "date": today,
                "total_channels": len(channels),
                "healthy_channels": healthy,
                "auto_disabled": disabled,
                "auto_restarts": restarts,
                "error_rate": rate,
                "balance": remaining,
                "manual_interventions": 0,
            })

            # 仅发送成功才标记“今日已发送”；失败留给下个周期重试
            if sent:
                self.autofix.state["last_daily_report"] = today
                self.autofix._save_state()
            return sent
        # 今日已发送过（或无需发送）→ 视为成功
        return True

# ═══════════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════════

def _acquire_single_instance():
    """Acquire a process-lifetime Windows mutex; return None for a duplicate."""
    if sys.platform != "win32":
        return True
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateMutexW(None, False, "Local\\NewAPIGuardian")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return None
    return handle


if __name__ == "__main__":
    _instance_handle = _acquire_single_instance()
    if _instance_handle is None:
        logger.info("Guardian already running; duplicate instance exiting")
        raise SystemExit(75)
    guardian = Guardian()
    guardian.run()
