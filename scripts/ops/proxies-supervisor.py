#!/usr/bin/env python3
"""proxies-supervisor — 本地 AI 网关代理的自愈看护（8787/8788/3003）。

背景（2026-08-05 事故）：agentrouter-proxy / converter 曾宕机 ~3 小时无人发现——
Guardian 的本地代理探针已按用户决定禁用，代理本身又没有自启动/看护。
本脚本补齐这一层：定时探测端口，挂了按 guardian 同款方式拉起（密钥走环境变量，
按脚本名精确杀旧进程，CREATE_NO_WINDOW）。崩溃自愈；进程卡死但端口开着的情况
不在本层处理（由 newapi-local-smoke 的周期实测暴露）。

设计参考：本机既有 start.bat 重试循环 + Task Scheduler conhost --headless 常驻模式。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import sys
import time
from datetime import datetime
from pathlib import Path
import urllib.request
import ctypes
import faulthandler

GUARDIAN_DIR = Path.home() / ".omp" / "guardian"
SECRETS_FILE = GUARDIAN_DIR / "secrets.json"
LOG_FILE = GUARDIAN_DIR / "proxies-supervisor.log"
STATUS_FILE = GUARDIAN_DIR / "supervisor-status.json"
CRASH_LOG = GUARDIAN_DIR / "proxies-supervisor-crash.log"


def _enable_faulthandler() -> None:
    """把崩溃栈固定落盘，不依赖调用方是否给了 stderr。

    2026-08-13：模块级 `faulthandler.enable()` 在 pythonw.exe 下抛
    RuntimeError: sys.stderr is None（无 console 时 sys.stderr 为 None），
    进程在 supervise() 之前就 exit 1。HKCU Run 的 `OMPProxiesSupervisor` 正是
    pythonw 入口，因此自 08-07 引入 faulthandler 起该"唯一持久入口"一直静默失效，
    常驻实例实际来自 Startup 的 conhost --headless + bat（有 console）兜底。
    实测：pythonw 跑本模块 exit=1；改为写文件后 exit=0 并进入看护循环。

    用裸 fd 而非文件对象：faulthandler 要求目标在进程生命周期内保持打开，
    模块级文件对象会在解释器退出时触发 ResourceWarning（测试以 -W error 运行）。
    """
    try:
        GUARDIAN_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(CRASH_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    except OSError:
        if sys.stderr is not None:
            faulthandler.enable()
        return
    faulthandler.enable(file=fd)


_enable_faulthandler()

CHECK_INTERVAL_S = 30
MAX_RESTARTS_PER_HOUR = 5
PYTHON = "C:/Users/zhugu/scoop/apps/python313/current/python.exe"
MUTEX_NAME = "Local\\OMPProxiesSupervisor"  # 单实例互斥体名（见 acquire_single_instance）

# new-api.db 每日备份（2026-08-05 用户批准）：SQLite 在线 backup API，保留 7 份。
NEWAPI_DB = Path("C:/Users/zhugu/.new-api-local/new-api.db")
BACKUP_DIR = NEWAPI_DB.parent / "backups"
BACKUP_KEEP = 7
BACKUP_HOUR = 3  # 每天 03:00 后第一次循环执行
SESSION_KEEP = 10  # user_sessions 每日清理：删除已过期 + 保留最近活跃 N 条，防 50 上限打满后 409

try:
    # utf-8-sig：第三方工具可能写入 BOM（见 guardian.py 同款踩坑）
    SECRETS = json.loads(SECRETS_FILE.read_text(encoding="utf-8-sig"))
except (OSError, ValueError):
    SECRETS = {}

BIND_HOST = SECRETS.get("local_proxy_bind_host") or "0.0.0.0"
PROBE_HOST = "127.0.0.1" if BIND_HOST in {"0.0.0.0", "::"} else BIND_HOST

PROXIES = {
    "agentrouter": {
        "port": 8788,
        "dir": "C:/Users/zhugu/.kimi-code/proxies/agentrouter-proxy",
        "cmd": [PYTHON, "agentrouter-proxy.py", "--host", BIND_HOST, "--port", "8788", "--log", "proxy.log"],
        # 2026-09-04: 上游 ps.air-outer.com / agentrouter.org 对家宽 IP 触发阿里云
        # WAF 全量 JS 挑战 → 经 Clash mixed-port 7897 走 Agentrouter-EG 专属组
        # （见 Clash Verge per-profile merge mc4PF6D8TBKv.yaml + runtime 注入）。
        # NO_PROXY 保住本地回环与 Tailscale 入口，防自环。
        "env": {
            "AGENTROUTER_PROXY_KEY": SECRETS.get("agentrouter_proxy_key", ""),
            "HTTP_PROXY": "http://127.0.0.1:7897",
            "HTTPS_PROXY": "http://127.0.0.1:7897",
            "NO_PROXY": "localhost,127.0.0.1,::1,100.64.0.0/10",
        },
        "proc": "python.exe",
        "match": "agentrouter-proxy.py",
    },
    # codebuddy/converter (8787) 已删除（2026-08-12）：WorkBuddy 上游 8-11 起
    # 流式 11140 不可用，用户决定弃用 WorkBuddy 渠道（NewAPI ch44 已删）。
    "omp-ttft": {
        "port": 3003,
        "probe_host": "127.0.0.1",
        "dir": "C:/Users/zhugu/.omp/guardian",
        "cmd": ["node", "C:/Users/zhugu/.omp/guardian/omp-ttft-gateway.cjs"],
        "env": {
            "OMP_TTFT_HOST": "127.0.0.1",
            "OMP_TTFT_PORT": "3003",
            "OMP_TTFT_UPSTREAM_HOST": "127.0.0.1",
            "OMP_TTFT_UPSTREAM_PORT": "3002",
            "OMP_TTFT_TIMEOUT_MS": "60000",
            "OMP_TTFT_HEADER_TIMEOUT_MS": "60000",
        },
        "proc": "node.exe",
        "match": "omp-ttft-gateway.cjs",
    },
    # justwoker-relay（8790）：NewAPI ch94/95 上游专用出口中转（2026-09-07 加入）。
    # 上游 api.justwoker.icu CF 封家宽直连（error 1010）但放行 Clash 悍刀行出口；
    # NewAPI 无 per-channel 代理，全局代理会拖垮 kimi 等直连渠道，故本地中转。
    # 上游 09-07 换组：Claude 系下架，改 gpt-5.6-luna/sol/terra + Anthropic 面
    # （/v1/messages，chat/completions 面 CF 拦截）。中转注入 Chrome UA 过 CF。
    "justwoker-relay": {
        "port": 8790,
        "probe_host": "127.0.0.1",
        "dir": "C:/Users/zhugu/.kimi-code/proxies/justwoker-relay",
        "cmd": ["node", "justwoker-relay.cjs"],
        "env": {},
        "proc": "node.exe",
        "match": "justwoker-relay\\.cjs",
    },
    # cc-switch 本地代理（15721，OMP 主链路）：仅进程级自愈——崩溃时
    # 重启 exe，不触碰本体代码/配置/DB。cc-switch 启动时自动恢复代理接管
    # 状态（restore_proxy_state_on_startup）。当前无任何自启动/看护机制。
    "cc-switch-proxy": {
        "port": 15721,
        "probe_host": "127.0.0.1",
        "dir": "C:/Users/zhugu/AppData/Local/CC Switch",
        "cmd": ["C:/Users/zhugu/AppData/Local/CC Switch/cc-switch.exe"],
        "env": {},
        "proc": "cc-switch.exe",
        "match": "cc-switch\\.exe",
    },
    # codex-relay：NewAPI ch73(zzzcoding)/ch74(sharedchat) 上游，由 supervisor 看护
    # （2026-08-08 加入；此前仅靠 Task Scheduler LogonTrigger 拉起，崩溃无自愈）
    # cmd 必须用绝对脚本路径：kill_stale 是按 CommandLine 匹配 match 正则的，
    # 相对路径（靠 cwd 生效）不会出现在命令行里，match 永不命中 → stale relay
    # 从不被清理，重启时新实例靠 SO_REUSEADDR 叠加绑同端口（2026-08-13 修）。
    "codex-relay-15999": {
        "port": 15999,
        "probe_host": "127.0.0.1",
        "dir": "C:/Users/zhugu/.omp/guardian/codex-relay-15999",
        "cmd": [PYTHON, "C:/Users/zhugu/.omp/guardian/codex-relay-15999/codex-relay.py",
                "--port", "15999",
                "--log-file", "C:/Users/zhugu/.omp/guardian/codex-relay.log",
                "--upstream", "https://api.zzzcoding.org/responses",
                "--secret-name", "zzzcoding_codex_key"],
        "env": {"CODEX_RELAY_SECRET_NAME": "zzzcoding_codex_key"},
        "proc": "python.exe",
        "match": "codex-relay-15999[\\\\/]codex-relay\\.py",
    },
    "codex-relay-16000": {
        "port": 16000,
        "probe_host": "127.0.0.1",
        "dir": "C:/Users/zhugu/.omp/guardian/codex-relay-16000",
        "cmd": [PYTHON, "C:/Users/zhugu/.omp/guardian/codex-relay-16000/codex-relay.py",
                "--port", "16000",
                "--log-file", "C:/Users/zhugu/.omp/guardian/sharedchat-codex-relay.log",
                "--upstream", "https://new.sharedchat.cc/codex/v1/responses",
                "--secret-name", "sharedchat_codex_key"],
        "env": {"CODEX_RELAY_SECRET_NAME": "sharedchat_codex_key"},
        "proc": "python.exe",
        "match": "codex-relay-16000[\\\\/]codex-relay\\.py",
    },
    # mistral-conversations-relay：glm-5-2 仅经 Mistral /v1/conversations 提供，
    # 本 relay 做 OpenAI chat/completions 格式转换，供 NewAPI 渠道使用（2026-08-16 加入）。
    "mistral-relay-16001": {
        "port": 16001,
        "probe_host": "127.0.0.1",
        "dir": "C:/Users/zhugu/.omp/guardian/mistral-relay-16001",
        "cmd": [PYTHON, "C:/Users/zhugu/.omp/guardian/mistral-relay-16001/mistral-conversations-relay.py",
                "--port", "16001",
                "--log-file", "C:/Users/zhugu/.omp/guardian/mistral-relay.log"],
        "env": {"MISTRAL_RELAY_SECRET_NAME": "mistral_glm_key"},
        "proc": "python.exe",
        "match": "mistral-relay-16001[\\\\/]mistral-conversations-relay\\.py",
    },
}

PROXIES["anyrouter"] = {
    "port": 8789,
    # 8789 只绑回环（OMP slow 链专用），不能用 tailnet 地址探测
    "probe_host": "127.0.0.1",
    "dir": "C:/Users/zhugu/.kimi-code/proxies/anyrouter-proxy",
    # 绝对路径启动，使 kill_stale 能锚定到本目录，避免误杀其他 proxy.cjs
    "cmd": ["node", "C:/Users/zhugu/.kimi-code/proxies/anyrouter-proxy/proxy.cjs"],
    # proxy.cjs 自己从 secrets.json 读 key；env 仅为通过启动时的密钥缺失检查
    "env": {"ANYROUTER_PROXY_KEY": SECRETS.get("anyrouter_proxy_key", "")},
    "proc": "node.exe",
    "match": "anyrouter-proxy[\\\\/]proxy\\.cjs",
}

# Telegram 告警：端口不可达且自愈失败/超预算时通知，30 分钟冷却防风暴。
TELEGRAM_TOKEN = str(SECRETS.get("telegram_token", ""))
TELEGRAM_CHAT_ID = str(SECRETS.get("telegram_chat_id", ""))
ALERT_COOLDOWN_S = 1800
ALERT_RETRY_S = 60  # Failed deliveries retry without a per-loop alert storm.
_alert_times: dict[str, float] = {}
_alert_attempt_times: dict[str, float] = {}


def send_telegram(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        payload = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.load(resp)
            return resp.status == 200 and isinstance(payload, dict) and payload.get("ok") is True
    except Exception as e:  # noqa: BLE001 — 告警失败不允许影响看护循环
        log(f"Telegram 告警发送失败: {e}")
        return False


def alert(name: str, text: str) -> None:
    now = time.time()
    if name in _alert_times and now - _alert_times[name] < ALERT_COOLDOWN_S:
        return
    if name in _alert_attempt_times and now - _alert_attempt_times[name] < ALERT_RETRY_S:
        return
    _alert_attempt_times[name] = now
    if send_telegram(text):
        _alert_times[name] = now

_restart_times: dict[str, list[float]] = {}


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        # 简单轮转：超过 1MB 截断保留尾部一半
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 1024 * 1024:
            tail = LOG_FILE.read_bytes()[-512 * 1024:]
            LOG_FILE.write_bytes(tail)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def port_open(port: int, host: str | None = None) -> bool:
    try:
        with socket.create_connection((host or PROBE_HOST, port), timeout=2):
            return True
    except OSError:
        return False


def kill_stale(proc_name: str, script_match: str) -> None:
    ps = (
        f"Get-CimInstance Win32_Process -Filter \"Name='{proc_name}'\" | "
        f"Where-Object {{ $_.CommandLine -match '{script_match}' }} | "
        f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def restart_allowed(name: str) -> bool:
    now = time.time()
    times = [t for t in _restart_times.get(name, []) if now - t < 3600]
    _restart_times[name] = times
    if len(times) >= MAX_RESTARTS_PER_HOUR:
        return False
    times.append(now)
    return True
def restarts_last_hour(name: str) -> int:
    now = time.time()
    recent = [stamp for stamp in _restart_times.get(name, []) if now - stamp < 3600]
    _restart_times[name] = recent
    return len(recent)


def service_status(*, healthy: bool, restart_blocked: bool, last_error: str | None,
                   restarts_last_hour: int) -> dict:
    return {
        "healthy": healthy,
        "restartBlocked": restart_blocked,
        "lastError": last_error,
        "restartsLastHour": restarts_last_hour,
    }


def write_status(services: dict[str, dict], restarts: dict[str, int], last_backup: str) -> None:
    payload = {
        "schema_version": 2,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "pid": os.getpid(),
        "bind_host": BIND_HOST,
        "services": services,
        "restarts_today": restarts,
        "last_backup": last_backup,
    }
    fd, tmp_name = tempfile.mkstemp(prefix="supervisor-status.", suffix=".tmp", dir=GUARDIAN_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, STATUS_FILE)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass




def restore_restart_state() -> tuple[str, str, dict[str, int]]:
    """Keep restart budgets and completed daily maintenance across restarts."""
    today = time.strftime("%Y-%m-%d")
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        age = time.time() - datetime.fromisoformat(data["ts"]).timestamp()
        if not 0 <= age < 86400:
            raise ValueError("snapshot outside recovery window")
        services = data["services"]
        counts = data["restarts_today"]
        if not isinstance(services, dict) or not isinstance(counts, dict):
            raise ValueError("invalid restart snapshot")
        if age < 3600:
            recovered = {}
            for name in PROXIES:
                count = services.get(name, {}).get("restartsLastHour", 0)
                if type(count) is not int or count < 0:
                    raise ValueError("invalid hourly restart count")
                # Exact restart times are unavailable in schema v2. Retain the
                # budget conservatively for an hour after this snapshot.
                recovered[name] = [time.time() - age] * min(count, MAX_RESTARTS_PER_HOUR)
            _restart_times.update(recovered)
        same_day = data["ts"][:10] == today
        daily = {name: count for name, count in counts.items()
                 if name in PROXIES and type(count) is int and count >= 0} if same_day else {}
        backup = today if data.get("last_backup") == today else ""
        return backup, today, daily
    except FileNotFoundError:
        return "", today, {}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        log("supervisor restart snapshot unavailable or invalid; using fresh counters")
        return "", today, {}


def supervise() -> None:
    log(f"supervisor 启动，探测 {PROBE_HOST}，绑定 {BIND_HOST}")
    for name, info in PROXIES.items():
        if info.get("env") and any(v == "" for v in info["env"].values()):
            log(f"警告: {name} 的密钥在 secrets.json 中缺失，启动可能鉴权失败")
    last_backup_date, restarts_day, restarts_today = restore_restart_state()
    while True:
        today = time.strftime("%Y-%m-%d")
        if today != restarts_day:
            restarts_day = today
            restarts_today = {}
        if time.localtime().tm_hour >= BACKUP_HOUR and today != last_backup_date:
            try:
                backup_newapi_db(today)
                last_backup_date = today
            except Exception as e:  # noqa: BLE001
                log(f"new-api.db 备份异常: {e}")
            try:
                cleanup_user_sessions()
            except Exception as e:  # noqa: BLE001
                log(f"user_sessions 清理异常: {e}")

        services: dict[str, dict] = {}
        for name, info in PROXIES.items():
            probe_host = info.get("probe_host")
            healthy = port_open(info["port"], probe_host)
            restart_blocked = False
            last_error: str | None = None
            if not healthy:
                if not restart_allowed(name):
                    restart_blocked = True
                    last_error = "restart limit reached"
                    log(f"{name} 一小时内重启已达 {MAX_RESTARTS_PER_HOUR} 次，停止自愈，需人工检查")
                    alert(name, f"⚠️ 本地代理 {name} 端口 {info['port']} 不可达，且一小时内重启已达 {MAX_RESTARTS_PER_HOUR} 次，已停止自愈，需人工检查")
                else:
                    log(f"{name} 端口 {info['port']} 不可达，重启")
                    try:
                        kill_stale(info["proc"], info["match"])
                        time.sleep(2)
                        # 单元素 cmd（exe 绝对/相对路径）跳过 script 存在性检查；
                        # 多元素 cmd（解释器 + 脚本）检查 dir 下脚本存在。
                        if len(info["cmd"]) > 1:
                            script = Path(info["dir"]) / info["cmd"][1]
                            if not script.exists():
                                raise FileNotFoundError(str(script))
                        env = {**os.environ, **info["env"]}
                        subprocess.Popen(
                            info["cmd"],
                            cwd=info["dir"],
                            env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                        )
                        restarts_today[name] = restarts_today.get(name, 0) + 1
                        time.sleep(4)
                        healthy = port_open(info["port"], probe_host)
                        if not healthy:
                            last_error = "port unavailable after restart"
                            alert(name, f"⚠️ 本地代理 {name} 端口 {info['port']} 重启后仍不可达，请检查 {info['dir']} 日志")
                    except Exception as e:  # noqa: BLE001 — 看护循环不允许退出
                        last_error = f"restart failed: {type(e).__name__}: {e}"
                        log(f"{name} 重启异常: {last_error}")
            services[name] = service_status(
                healthy=healthy,
                restart_blocked=restart_blocked,
                last_error=last_error,
                restarts_last_hour=restarts_last_hour(name),
            )
        write_status(services, restarts_today, last_backup_date)
        time.sleep(CHECK_INTERVAL_S)
def cleanup_user_sessions() -> tuple[int, int]:
    """删除已过期的 user_sessions，并只保留最近活跃 SESSION_KEEP 条。

    背景：NewAPI user_sessions 上限 50，打满后所有登录 409 AUTH_SESSION_LIMIT
    （重启不清）。Guardian/管理端登录会重新建立会话，删除安全。
    """
    import sqlite3

    conn = sqlite3.connect(str(NEWAPI_DB), timeout=30)
    try:
        now = int(time.time())
        expired = conn.execute("DELETE FROM user_sessions WHERE expires_at < ?", (now,)).rowcount
        overflow = conn.execute(
            "DELETE FROM user_sessions WHERE sid NOT IN "
            "(SELECT sid FROM user_sessions ORDER BY last_active_at DESC LIMIT ?)",
            (SESSION_KEEP,),
        ).rowcount
        conn.commit()
        if expired or overflow:
            log(f"user_sessions 清理: 过期 {expired}，超量 {overflow}（保留最近 {SESSION_KEEP} 条）")
        return expired, overflow
    finally:
        conn.close()


def backup_newapi_db(today: str) -> None:
    """SQLite 在线备份 new-api.db 到 backups/，保留最新 BACKUP_KEEP 份。"""
    import sqlite3

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dst = BACKUP_DIR / f"new-api-{today}.db"
    src = sqlite3.connect(f"file:{NEWAPI_DB}?mode=ro", uri=True, timeout=30)
    try:
        out = sqlite3.connect(str(dst), timeout=30)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    size_mb = dst.stat().st_size / 1024 / 1024
    backups = sorted(BACKUP_DIR.glob("new-api-*.db"))
    pruned = 0
    for old in backups[:-BACKUP_KEEP]:
        old.unlink()
        pruned += 1
    log(f"new-api.db 备份完成: {dst.name} ({size_mb:.1f}MB)，清理旧备份 {pruned} 份")


def acquire_single_instance(name: str = MUTEX_NAME):
    """Hold a process-lifetime Windows mutex; return None for a duplicate.

    2026-08-13：bInitialOwner 必须为 True。此前传 False —— 没有任何进程真正
    "拥有"该 mutex，于是每个重复实例的 WaitForSingleObject(0) 都会静默取得
    所有权，进程退出时又不释放 → mutex 变成 abandoned，下一次启动命中下方
    接管分支，在原 supervisor 仍存活时开出第二个 owner（即 2026-08-06
    "双 owner" 事故：两个 supervisor 同时杀/拉同一批代理）。
    隔离复现：bInitialOwner=False 时第 2 个探针 wait=0x80 直接接管；
    改 True 后两个探针均为 0x102(WAIT_TIMEOUT)，正确判重。

    name 可覆盖，仅供测试隔离用——生产路径一律使用默认 MUTEX_NAME。
    """
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, True, name)
    if not handle:
        raise ctypes.WinError()
    if kernel32.GetLastError() == 183:
        # ERROR_ALREADY_EXISTS：区分"持有者存活"与"abandoned"（持有者被强杀/崩溃）。
        # abandoned 的 mutex 不再有守护者，本进程应接管而非退出——
        # 否则强杀 supervisor 后任何重启方式（Run 键/计划任务/手动）都会静默失败。
        wait = kernel32.WaitForSingleObject(handle, 0)
        if wait == 0x80:  # WAIT_ABANDONED：旧持有者已死，接管
            return handle
        kernel32.CloseHandle(handle)
        return None
    return handle


if __name__ == "__main__":
    _instance_handle = acquire_single_instance()
    if _instance_handle is not None:
        supervise()
