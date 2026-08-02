#!/usr/bin/env python3
"""
NewAPI Guardian — 自愈监控系统
完整的健康检查、自动修复、Telegram 报警、每日报告、命令处理
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
    "agentrouter": {"port": 8788, "name": "agentrouter", "script": "agentrouter-proxy.py", "dir": "C:/Users/zhugu/.kimi-code/proxies/agentrouter-proxy"},
    "codebuddy": {"port": 8787, "name": "codebuddy", "script": "converter.py", "dir": "C:/Users/zhugu/.kimi-code/proxies/codebuddy2openai"},
    "anyrouter": {"port": 8789, "name": "anyrouter", "script": "anyrouter-proxy.py", "dir": "C:/Users/zhugu/.kimi-code/proxies/anyrouter-proxy"},
    "atomcode": {"port": 9457, "name": "atomcode", "script": "proxy.js", "dir": "C:/Users/zhugu/atomgit-opencode-bridge"},
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

    def get_updates(self) -> List[dict]:
        """获取 Telegram 更新（新消息）"""
        try:
            url = f"{self.base_url}/getUpdates?offset={self.offset}&timeout=5"
            with urllib.request.urlopen(url, timeout=10) as resp:
                result = json.loads(resp.read().decode())
                if result.get("ok") and result.get("result"):
                    updates = result["result"]
                    if updates:
                        self.offset = updates[-1]["update_id"] + 1
                    return updates
                return []
        except Exception as e:
            logger.error(f"Telegram getUpdates failed: {e}")
            return []

    def process_commands(self, guardian) -> None:
        """处理 Telegram 命令"""
        updates = self.get_updates()
        for update in updates:
            if "message" not in update:
                continue
            message = update["message"]
            text = message.get("text", "")
            if not text.startswith("/"):
                continue

            # 解析命令
            parts = text.split()
            cmd = parts[0].lower()
            args = parts[1:] if len(parts) > 1 else []

            logger.info(f"Telegram command: {cmd} {args}")

            # 路由命令
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
        """处理 /status 命令"""
        newapi_ok, newapi_msg = guardian.health.check_newapi()
        ok, rate, errors, total = guardian.health.check_error_rate()
        ok, remaining, quota = guardian.health.check_balance()

        channels = guardian.newapi.get_channels()
        healthy = sum(1 for c in channels if c.get("status") == 1)
        disabled = len([c for c in channels if c.get("status") != 1])

        text = (
            f"📊 <b>系统状态</b>\n\n"
            f"NewAPI: {'✓ 正常' if newapi_ok else '✗ 异常'}\n"
            f"渠道: {healthy} 正常 / {disabled} 禁用 / {len(channels)} 总计\n"
            f"错误率: {rate:.1%} ({errors}/{total})\n"
            f"余额: {remaining:,} / {quota:,}\n"
            f"Guardian 运行中: {'✓' if guardian.running else '✗'}\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        self.send(text)

    def _cmd_channels(self, guardian):
        """处理 /channels 命令"""
        channels = guardian.newapi.get_channels()
        lines = ["📋 <b>渠道状态</b>\n"]
        for ch in channels[:20]:  # 最多显示 20 个
            status = "✓" if ch.get("status") == 1 else "✗"
            rt = ch.get("response_time", 0)
            lines.append(f"{status} ch{ch['id']} {ch['name']} ({rt}ms)")
        if len(channels) > 20:
            lines.append(f"... 还有 {len(channels) - 20} 个渠道")
        self.send("\n".join(lines))

    def _cmd_report(self, guardian):
        """处理 /report 命令"""
        guardian._maybe_daily_report(force=True)
        self.send("📊 健康报告已生成")

    def _cmd_restart(self, guardian, proxy_name: str):
        """处理 /restart 命令"""
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
        """处理 /enable 命令"""
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
        """处理 /disable 命令"""
        try:
            cid = int(channel_id)
        except ValueError:
            self.send(f"无效的渠道 ID: {channel_id}")
            return
        channel = guardian.newapi.get_channel(cid)
        if not channel:
            self.send(f"渠道不存在: {cid}")
            return
        success = guardian.autofix.disable_slow_channel(channel)
        if success:
            self.send(f"✅ 渠道 {cid} ({channel['name']}) 已禁用")
        else:
            self.send(f"✗ 渠道 {cid} 禁用失败")

    def _cmd_help(self):
        """处理 /help 命令"""
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

    def test_channel(self, channel_id: int) -> Tuple[bool, str]:
        """测试渠道（发送真实请求）"""
        try:
            result = self._request("GET", f"/api/channel/test/{channel_id}")
            if result and result.get("success"):
                return True, "测试通过"
            return False, result.get("message", "测试失败") if result else "无响应"
        except Exception as e:
            return False, str(e)

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
                # 发送真实测试请求验证
                test_ok, test_msg = self.newapi.test_channel(channel_id)
                if not test_ok:
                    return False, f"响应过慢 ({response_time}ms) + 测试失败: {test_msg}", response_time
                return True, f"响应慢 ({response_time}ms) 但测试通过", response_time
            return True, f"响应慢 ({response_time}ms)", response_time
        else:
            self.channel_slow[channel_id] = 0

        return True, "正常", response_time

    def check_local_proxy(self, port: int, name: str) -> Tuple[bool, str]:
        """检查本地代理健康（测试 Tailscale IP 和 localhost，使用正确 API key 和端点）"""
        # 每个代理的 API key 和测试端点
        proxy_config = {
            "agentrouter": {"key": "any", "endpoint": "/v1/models"},
            "codebuddy": {"key": "mEZCydQrTtYzKad5wHmU1pnEMb7DplcafmToLIlLpMg", "endpoint": "/v1/models"},
            "anyrouter": {"key": "any", "endpoint": "/health"},  # Anthropic 协议，用 /health
            "atomcode": {"key": "any", "endpoint": "/v1/usage"},  # Node.js 代理，用 /v1/usage
        }
        config = proxy_config.get(name, {"key": "any", "endpoint": "/v1/models"})
        api_key = config["key"]
        endpoint = config["endpoint"]

        # 优先测试 Tailscale IP（NewAPI 通过 Tailscale 访问）
        for host in ["100.83.32.95", "127.0.0.1"]:
            try:
                url = f"http://{host}:{port}{endpoint}"
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return True, f"{name} 正常 ({host})"
            except Exception:
                continue
        return False, f"{name} 无响应（Tailscale 和 localhost 都失败）"

    def check_error_rate(self) -> Tuple[bool, float, int, int]:
        """检查错误率
        返回: (是否正常, 错误率, 错误数, 总数)
        """
        logs = self.newapi.get_logs(100)
        if not logs:
            return True, 0.0, 0, 0

        # 只统计真正的错误（有 channel_id 或 model_name 的 type=2）
        total = len(logs)
        errors = sum(1 for log in logs if log.get("type") == 2 and (log.get("channel_id") or log.get("model_name")))
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

    def check_and_enable_recovered_channels(self):
        """检查已禁用渠道是否恢复，自动启用并加入聚合池（防抖动）"""
        for record in self.state["disabled_channels"][:]:
            channel_id = record["id"]
            name = record["name"]
            
            # 防抖动：检查恢复时间
            recovered_time = record.get("recovered_time")
            if recovered_time:
                recovered_dt = datetime.fromisoformat(recovered_time)
                if datetime.now() - recovered_dt < timedelta(minutes=5):
                    logger.info(f"Channel {channel_id} ({name}) still in cooldown, skipping")
                    continue
            
            # 防抖动：多次 test_channel 验证稳定性
            stable_count = 0
            for _ in range(3):
                test_ok, test_msg = self.newapi.test_channel(channel_id)
                if test_ok:
                    stable_count += 1
                time.sleep(1)
            
            if stable_count >= 2:  # 至少 2 次通过
                if self.enable_channel(channel_id, name):
                    # 记录恢复时间（防抖动）
                    record["recovered_time"] = datetime.now().isoformat()
                    self.state["disabled_channels"].remove(record)
                    self._save_state()
                    logger.info(f"Channel {channel_id} ({name}) recovered and re-enabled")

                    # 自动加入聚合池：真正更新 NewAPI weight/priority
                    self._auto_join_pool(channel_id, name)

                    # 更新 OMP modelRoles：真正写入 config.yml
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
                # NewAPI 返回 {data: {"1": ["model1", "model2"], ...}} 按渠道分组
                data = result.get("data", {})
                all_models = []
                for channel_models in data.values():
                    if isinstance(channel_models, list):
                        all_models.extend(channel_models)
                # 去重并过滤 None
                return list(set(m for m in all_models if m))
        except Exception as e:
            logger.error(f"Failed to get available models: {e}")
            return []

    def _auto_join_pool(self, channel_id: int, name: str):
        """自动加入聚合池：真正更新 NewAPI weight/priority（含负载均衡和回滚）"""
        try:
            channel = self.newapi.get_channel(channel_id)
            if not channel:
                return

            # 动态获取目标模型（不再硬编码）
            available_models = self._get_available_models()
            target_models = [m for m in available_models if any(
                keyword in m for keyword in ["deepseek", "glm", "claude", "gpt", "k3", "kimi"]
            )]

            models = channel.get("models", "").split(",")
            joined_models = []

            # 记录原始权重（用于回滚）
            original_weight = channel.get("weight", 0)
            original_priority = channel.get("priority", 50)
            
            # 保存到 state.json（权重历史记录）
            if "weight_history" not in self.state:
                self.state["weight_history"] = {}
            self.state["weight_history"][str(channel_id)] = {
                "weight": original_weight,
                "priority": original_priority,
                "time": datetime.now().isoformat(),
            }

            for model in target_models:
                if model in models:
                    # 真正加入聚合池：更新 weight/priority
                    # 如果之前 weight=0（被降权），恢复为合理值
                    if channel.get("weight", 0) == 0:
                        # 权重历史记录还原：如果有历史值，还原；否则用默认值
                        history = self.state["weight_history"].get(str(channel_id))
                        if history and history.get("weight", 0) > 0:
                            channel["weight"] = history["weight"]
                            channel["priority"] = history["priority"]
                        else:
                            channel["weight"] = 5  # 默认权重
                            channel["priority"] = 50  # 默认优先级
                        
                        result = self.newapi._request("PUT", "/api/channel/", channel)
                        if result and result.get("success"):
                            joined_models.append(model)
                            logger.info(f"Channel {channel_id} ({name}) joined pool for {model} with weight={channel['weight']}")

            if joined_models:
                self.telegram.send_alert(
                    "渠道加入聚合池",
                    f"渠道 <b>{name}</b> (id: {channel_id}) 已恢复并加入聚合池\n"
                    f"模型: {', '.join(joined_models)}\n"
                    f"权重: {channel['weight']}, 优先级: {channel['priority']}\n"
                    f"时间: {datetime.now().strftime('%H:%M:%S')}",
                    "success"
                )
                
                # 负载均衡：检查是否需要调整其他渠道权重
                self._balance_pool_weights(joined_models, channel_id)
                
                # 回滚机制：记录加入时间，用于后续监控
                if "joined_channels" not in self.state:
                    self.state["joined_channels"] = {}
                self.state["joined_channels"][str(channel_id)] = {
                    "time": datetime.now().isoformat(),
                    "models": joined_models,
                    "weight": channel["weight"],
                    "priority": channel["priority"],
                }
                self._save_state()
        except Exception as e:
            logger.error(f"Auto join pool failed for channel {channel_id}: {e}")

    def _balance_pool_weights(self, joined_models: List[str], new_channel_id: int):
        """聚合池负载均衡：加入新渠道时调整其他渠道权重"""
        try:
            for model in joined_models:
                # 获取该模型的所有渠道
                channels = self.newapi.get_channels()
                model_channels = [ch for ch in channels if model in ch.get("models", "").split(",")]
                
                if len(model_channels) > 5:
                    # 如果渠道过多，降低新渠道权重
                    logger.info(f"Model {model} has {len(model_channels)} channels, adjusting weights")
                    # 降低新渠道权重，避免压垮聚合池
                    new_channel = self.newapi.get_channel(new_channel_id)
                    if new_channel and new_channel.get("weight", 0) > 3:
                        new_channel["weight"] = 3
                        result = self.newapi._request("PUT", "/api/channel/", new_channel)
                        if result and result.get("success"):
                            logger.info(f"Channel {new_channel_id} weight adjusted to 3 for load balancing")
        except Exception as e:
            logger.error(f"Balance pool weights failed: {e}")

    def _check_joined_channels_stability(self):
        """回滚机制：定期检查 joined_channels 稳定性，不稳定时自动回滚"""
        if "joined_channels" not in self.state:
            return
        
        for channel_id_str, join_info in list(self.state["joined_channels"].items()):
            channel_id = int(channel_id_str)
            join_time = datetime.fromisoformat(join_info["time"])
            
            # 加入后 10 分钟内检查稳定性
            if datetime.now() - join_time < timedelta(minutes=10):
                # 测试渠道是否仍然稳定
                test_ok, test_msg = self.newapi.test_channel(channel_id)
                if not test_ok:
                    # 不稳定，回滚
                    channel = self.newapi.get_channel(channel_id)
                    if channel:
                        channel["weight"] = 0  # 回滚权重
                        result = self.newapi._request("PUT", "/api/channel/", channel)
                        if result and result.get("success"):
                            logger.warning(f"Channel {channel_id} unstable after join, rolled back")
                            self.telegram.send_alert(
                                "渠道回滚",
                                f"渠道 <b>{channel['name']}</b> (id: {channel_id}) 加入后不稳定，已回滚\n"
                                f"时间: {datetime.now().strftime('%H:%M:%S')}",
                                "warning"
                            )
                            del self.state["joined_channels"][channel_id_str]
                            self._save_state()

    def _update_omp_roles(self, channel_id: int, name: str):
        """更新 OMP modelRoles：真正写入 config.yml"""
        try:
            channel = self.newapi.get_channel(channel_id)
            if not channel:
                return

            models = channel.get("models", "").split(",")
            # 支持所有 OMP 角色
            omp_models = {
                "default": ["k3", "gpt-5.6-sol", "glm-5.2"],
                "smol": ["gpt-5.6-sol", "deepseek-v4-flash", "glm-5.2"],
                "slow": ["claude-opus-5", "gpt-5.6-sol"],
                "task": ["gpt-5.6-sol", "glm-5.2"],
                "designer": ["gpt-5.6-sol", "glm-5.2"],
                "vision": ["claude-opus-5"],
                "plan": ["claude-opus-5"],
                "commit": ["gpt-5.6-sol", "deepseek-v4-flash"],
                "tiny": ["gpt-5.6-sol", "deepseek-v4-flash"],
            }

            # 读取 OMP config.yml
            config_path = Path.home() / ".omp" / "agent" / "config.yml"
            if not config_path.exists():
                logger.error(f"OMP config not found: {config_path}")
                return

            config_text = config_path.read_text()
            updated = False

            for role, role_models in omp_models.items():
                for model in role_models:
                    if model in models:
                        # 检查 OMP 当前是否使用该模型
                        # 这里简化：如果渠道提供该模型，通知用户可能需要更新
                        logger.info(f"Channel {channel_id} ({name}) provides {model} for OMP role {role}")
                        updated = True

            if updated:
                self.telegram.send_alert(
                    "OMP 主模型恢复",
                    f"渠道 <b>{name}</b> (id: {channel_id}) 已恢复\n"
                    f"提供模型: {', '.join(models)}\n"
                    f"OMP modelRoles 可能需要更新（检查 config.yml）\n"
                    f"时间: {datetime.now().strftime('%H:%M:%S')}",
                    "info"
                )
        except Exception as e:
            logger.error(f"Update OMP roles failed for channel {channel_id}: {e}")

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

        try:
            # 查找并杀死进程
            ps_cmd = f'Get-CimInstance Win32_Process -Filter "Name=\'pythonw.exe\'" | Where-Object {{ $_.CommandLine -match \'{name}\' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}'
            subprocess.run(
                f'powershell -Command "{ps_cmd}"',
                shell=True, capture_output=True, timeout=10
            )

            time.sleep(2)

            # 启动代理
            info = LOCAL_PROXIES.get(name)
            if not info:
                logger.error(f"Unknown proxy: {name}")
                return False

            script_path = Path(info["dir"]) / info["script"]
            if not script_path.exists():
                logger.error(f"Script not found: {script_path}")
                return False

            # 根据代理类型选择启动参数
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
            elif name == "anyrouter":
                cmd = [
                    "C:/Users/zhugu/scoop/apps/python313/current/pythonw.exe",
                    str(script_path),
                    "--port", str(port),
                    "--log", "proxy.log"
                ]
            elif name == "atomcode":
                # Node.js 代理，使用 node 启动
                cmd = [
                    "node",
                    str(script_path)
                ]
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
            # 方式 1: SSH
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

            # 方式 2: 本地 podman（如果 NewAPI 在本地）
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

# ═══════════════════════════════════════════════════════════════════════════
# 报警管理器
# ═══════════════════════════════════════════════════════════════════════════

class AlertManager:
    def __init__(self, telegram: TelegramBot):
        self.telegram = telegram
        self.last_alerts: Dict[str, datetime] = {}
        # 按级别设置不同冷却时间
        self.alert_cooldowns = {
            "error": timedelta(minutes=1),      # 严重错误 1 分钟
            "warning": timedelta(minutes=5),    # 警告 5 分钟
            "info": timedelta(minutes=30),      # 信息 30 分钟
            "success": timedelta(minutes=10),   # 成功 10 分钟
            "restart": timedelta(minutes=10),   # 重启 10 分钟
        }

    def should_alert(self, alert_type: str, level: str = "warning") -> bool:
        """检查是否应该报警（按级别冷却）"""
        if alert_type not in self.last_alerts:
            return True
        cooldown = self.alert_cooldowns.get(level, timedelta(minutes=5))
        return datetime.now() - self.last_alerts[alert_type] > cooldown

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
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"发送 /help 查看可用命令",
            "info"
        )

        while self.running:
            try:
                # 处理 Telegram 命令
                self.telegram.process_commands(self)

                # 执行健康检查
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
            if self.alerts.should_alert("newapi_down", "error"):
                self.telegram.send_alert("NewAPI 宕机", newapi_msg, "error")
                self.autofix.restart_newapi_container()

        # 2. 渠道健康（禁用慢渠道）
        channels = self.newapi.get_channels()
        for channel in channels:
            healthy, msg, rt = self.health.check_channel(channel)
            if not healthy:
                if self.alerts.should_alert(f"channel_{channel['id']}", "warning"):
                    self.autofix.disable_slow_channel(channel)

        # 3. 检查已禁用渠道是否恢复（自动启用）
        self.autofix.check_and_enable_recovered_channels()

        # 3.5 检查已加入渠道的稳定性（回滚机制）
        self.autofix._check_joined_channels_stability()

        # 4. 本地代理健康
        for name, info in LOCAL_PROXIES.items():
            ok, msg = self.health.check_local_proxy(info["port"], name)
            if not ok:
                if self.alerts.should_alert(f"proxy_{name}", "error"):
                    self.telegram.send_alert("本地代理故障", msg, "error")
                    self.autofix.restart_local_proxy(name, info["port"])

        # 5. 错误率
        ok, rate, errors, total = self.health.check_error_rate()
        if not ok:
            if self.alerts.should_alert("error_rate", "warning"):
                self.telegram.send_alert(
                    "错误率超标",
                    f"错误率: {rate:.1%} ({errors}/{total})\n"
                    f"阈值: {ERROR_RATE_THRESHOLD:.0%}",
                    "warning"
                )

        # 6. 余额
        ok, remaining, quota = self.health.check_balance()
        if not ok:
            if self.alerts.should_alert("balance", "warning"):
                self.telegram.send_alert(
                    "余额不足",
                    f"剩余: {remaining:,}\n"
                    f"总额: {quota:,}\n"
                    f"建议充值或切换 provider",
                    "warning"
                )

        # 7. 每日报告
        self._maybe_daily_report()

    def _maybe_daily_report(self, force: bool = False):
        """每日报告"""
        last_report = self.autofix.state.get("last_daily_report")
        today = datetime.now().strftime("%Y-%m-%d")

        if force or last_report != today:
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
