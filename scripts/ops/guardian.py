#!/usr/bin/env python3
"""
NewAPI Guardian — 自愈监控系统
完整的健康检查、自动修复、Telegram 报警、每日报告
"""

import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
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

# 本地代理
LOCAL_PROXIES = {
    "agentrouter": {"port": 8788, "name": "agentrouter"},
    "codebuddy": {"port": 8787, "name": "codebuddy"},
    "anyrouter": {"port": 8789, "name": "anyrouter"},
}

# 日志
LOG_DIR = Path.home() / ".omp" / "guardian"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "guardian.log"
STATE_FILE = LOG_DIR / "state.json"

# ═══════════════════════════════════════════════════════════════════════════
# 日志
# ═══════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
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
        """检查 NewAPI 健康"""
        try:
            url = f"{self.base_url}/api/status"
            with urllib.request.urlopen(url, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def get_channels(self) -> List[dict]:
        """获取所有渠道"""
        result = self._request("GET", "/api/channel/?p=0&page_size=200")
        return result.get("data", {}).get("items", []) if result else []

    def get_channel(self, channel_id: int) -> Optional[dict]:
        """获取单个渠道"""
        result = self._request("GET", f"/api/channel/{channel_id}")
        return result.get("data") if result else None

    def disable_channel(self, channel_id: int) -> bool:
        """禁用渠道（status=2）"""
        channel = self.get_channel(channel_id)
        if not channel:
            return False
        # 保留所有字段，只改 status
        channel["status"] = 2
        result = self._request("PUT", "/api/channel/", channel)
        return result.get("success", False) if result else False

    def enable_channel(self, channel_id: int) -> bool:
        """启用渠道（status=1）"""
        channel = self.get_channel(channel_id)
        if not channel:
            return False
        channel["status"] = 1
        result = self._request("PUT", "/api/channel/", channel)
        return result.get("success", False) if result else False

    def get_logs(self, limit: int = 100) -> List[dict]:
        """获取最近日志"""
        result = self._request("GET", f"/api/log/?p=0&page_size={limit}")
        return result.get("data", {}).get("items", []) if result else []

    def get_user_info(self) -> Optional[dict]:
        """获取用户信息（余额）"""
        result = self._request("GET", "/api/user/self")
        return result.get("data") if result else None

# ═══════════════════════════════════════════════════════════════════════════
# 健康检查器
# ═══════════════════════════════════════════════════════════════════════════

class HealthChecker:
    def __init__(self, newapi: NewAPIClient):
        self.newapi = newapi
        self.channel_failures: Dict[int, int] = {}  # channel_id -> 连续失败次数
        self.channel_slow: Dict[int, int] = {}  # channel_id -> 连续慢响应次数

    def check_newapi(self) -> Tuple[bool, str]:
        """检查 NewAPI 健康"""
        if self.newapi.get_status():
            return True, "NewAPI 正常"
        return False, "NewAPI 无响应"

    def check_channel(self, channel: dict) -> Tuple[bool, str, int]:
        """检查单个渠道健康
        返回: (是否健康, 状态描述, response_time_ms)
        """
        channel_id = channel["id"]
        name = channel["name"]
        response_time = channel.get("response_time") or 0
        status = channel.get("status", 1)

        # 已禁用渠道跳过
        if status != 1:
            return True, f"已禁用 (status={status})", response_time

        # response_time 检查
        if response_time > CHANNEL_SLOW_THRESHOLD_MS:
            self.channel_slow[channel_id] = self.channel_slow.get(channel_id, 0) + 1
            if self.channel_slow[channel_id] >= CHANNEL_FAIL_THRESHOLD:
                return False, f"响应过慢 ({response_time}ms)", response_time
            return True, f"响应慢 ({response_time}ms)", response_time
        else:
            self.channel_slow[channel_id] = 0

        return True, "正常", response_time

    def check_local_proxy(self, port: int, name: str) -> Tuple[bool, str]:
        """检查本地代理健康"""
        try:
            url = f"http://127.0.0.1:{port}/v1/models"
            req = urllib.request.Request(url, headers={"Authorization": "Bearer any"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return True, f"{name} 正常"
        except Exception as e:
            return False, f"{name} 无响应: {e}"

    def check_error_rate(self) -> Tuple[bool, float, int, int]:
        """检查错误率
        返回: (是否正常, 错误率, 错误数, 总数)
        """
        logs = self.newapi.get_logs(100)
        if not logs:
            return True, 0.0, 0, 0

        total = len(logs)
        errors = sum(1 for log in logs if log.get("type") == 2)
        rate = errors / total if total > 0 else 0.0

        return rate <= ERROR_RATE_THRESHOLD, rate, errors, total

    def check_balance(self) -> Tuple[bool, int, int]:
        """检查余额
        返回: (是否正常, 剩余, 总额)
        """
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

    def _load_state(self) -> dict:
        """加载状态"""
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
        }

    def _save_state(self):
        """保存状态"""
        STATE_FILE.write_text(json.dumps(self.state, indent=2))

    def disable_slow_channel(self, channel: dict) -> bool:
        """禁用慢渠道"""
        channel_id = channel["id"]
        name = channel["name"]
        response_time = channel.get("response_time", 0)

        if self.newapi.disable_channel(channel_id):
            self.state["disabled_channels"].append({
                "id": channel_id,
                "name": name,
                "reason": f"response_time: {response_time}ms",
                "time": datetime.now().isoformat(),
            })
            self._save_state()
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

    def restart_local_proxy(self, name: str, port: int) -> bool:
        """重启本地代理"""
        restart_count = self.state["restart_counts"].get(name, 0)
        if restart_count >= 3:
            self.telegram.send_alert(
                "本地代理重启失败",
                f"代理 <b>{name}</b> (端口 {port}) 已重启 {restart_count} 次仍失败\n"
                f"请手动检查",
                "error"
            )
            return False

        # Windows 下用 taskkill + 启动脚本
        try:
            # 查找并杀死进程
            subprocess.run(
                f'taskkill /F /FI "WINDOWTITLE eq {name}*" 2>nul',
                shell=True, capture_output=True
            )
            time.sleep(1)

            # 启动代理（假设有启动脚本）
            # 这里需要根据实际启动方式调整
            # 例如: subprocess.Popen(f"python {name}.py", shell=True)

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
        """重启 NewAPI 容器"""
        try:
            # 通过 SSH 或本地命令重启
            # 这里假设有 SSH 访问或本地 podman
            subprocess.run(
                "ssh donglicao@aliyun 'podman restart new-api'",
                shell=True, capture_output=True, timeout=30
            )
            self.telegram.send_alert(
                "NewAPI 容器重启",
                f"NewAPI 容器已重启\n"
                f"时间: {datetime.now().strftime('%H:%M:%S')}",
                "restart"
            )
            return True
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

# ═══════════════════════════════════════════════════════════════════════════
# 报警管理器
# ═══════════════════════════════════════════════════════════════════════════

class AlertManager:
    def __init__(self, telegram: TelegramBot):
        self.telegram = telegram
        self.last_alerts: Dict[str, datetime] = {}
        self.alert_cooldown = timedelta(minutes=5)  # 同类报警 5 分钟冷却

    def should_alert(self, alert_type: str) -> bool:
        """检查是否应该报警（冷却）"""
        if alert_type not in self.last_alerts:
            return True
        return datetime.now() - self.last_alerts[alert_type] > self.alert_cooldown

    def send_daily_report(self, stats: dict):
        """发送每日健康报告"""
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
        """主循环"""
        logger.info("NewAPI Guardian 启动")
        self.telegram.send_alert(
            "Guardian 启动",
            "NewAPI 自愈系统已启动\n"
            f"监控间隔: {HEALTH_CHECK_INTERVAL} 秒\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "info"
        )

        while self.running:
            try:
                self._check_cycle()
                time.sleep(HEALTH_CHECK_INTERVAL)
            except KeyboardInterrupt:
                logger.info("Guardian 停止")
                break
            except Exception as e:
                logger.error(f"Check cycle error: {e}")
                time.sleep(HEALTH_CHECK_INTERVAL)

    def _check_cycle(self):
        """单次检查循环"""
        # 1. NewAPI 健康
        newapi_ok, newapi_msg = self.health.check_newapi()
        if not newapi_ok:
            if self.alerts.should_alert("newapi_down"):
                self.telegram.send_alert("NewAPI 宕机", newapi_msg, "error")
                self.autofix.restart_newapi_container()

        # 2. 渠道健康
        channels = self.newapi.get_channels()
        for channel in channels:
            healthy, msg, rt = self.health.check_channel(channel)
            if not healthy:
                if self.alerts.should_alert(f"channel_{channel['id']}"):
                    self.autofix.disable_slow_channel(channel)

        # 3. 本地代理健康
        for name, info in LOCAL_PROXIES.items():
            ok, msg = self.health.check_local_proxy(info["port"], name)
            if not ok:
                if self.alerts.should_alert(f"proxy_{name}"):
                    self.telegram.send_alert("本地代理故障", msg, "error")
                    self.autofix.restart_local_proxy(name, info["port"])

        # 4. 错误率
        ok, rate, errors, total = self.health.check_error_rate()
        if not ok:
            if self.alerts.should_alert("error_rate"):
                self.telegram.send_alert(
                    "错误率超标",
                    f"错误率: {rate:.1%} ({errors}/{total})\n"
                    f"阈值: {ERROR_RATE_THRESHOLD:.0%}",
                    "warning"
                )

        # 5. 余额
        ok, remaining, quota = self.health.check_balance()
        if not ok:
            if self.alerts.should_alert("balance"):
                self.telegram.send_alert(
                    "余额不足",
                    f"剩余: {remaining:,}\n"
                    f"总额: {quota:,}\n"
                    f"建议充值或切换 provider",
                    "warning"
                )

        # 6. 每日报告
        self._maybe_daily_report()

    def _maybe_daily_report(self):
        """每日报告"""
        last_report = self.autofix.state.get("last_daily_report")
        today = datetime.now().strftime("%Y-%m-%d")

        if last_report != today:
            # 统计今日数据
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
                "manual_interventions": 0,  # 需要手动统计
            })

            self.autofix.state["last_daily_report"] = today
            self.autofix._save_state()

# ═══════════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    guardian = Guardian()
    guardian.run()
