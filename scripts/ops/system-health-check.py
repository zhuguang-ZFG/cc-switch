#!/usr/bin/env python3
"""system-health-check — 一键巡检 OMP × NewAPI × 本地代理全链路。

覆盖：NewAPI/TTFT/cc-switch/7 本地代理端口、guardian 心跳、supervisor 状态、
watchdog 崩溃记录、NewAPI 渠道健康、关键日志大小、磁盘余量。
退出码：0 = 全绿；1 = 有失败项（供计划任务/告警判断）。
用法：python scripts/ops/system-health-check.py [--json]
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
OMP = HOME / ".omp"
GUARDIAN = OMP / "guardian"
NEWAPI_LOCAL = HOME / ".new-api-local"
RESULT: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULT.append({"name": name, "ok": bool(ok), "detail": detail})
    mark = "OK " if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_status(url: str, timeout: float = 5.0) -> int:
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except Exception:
        return 0


def fresh_json(path: Path, stale_sec: int) -> tuple[bool, dict]:
    """返回 (是否新鲜, 解析后的内容)。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ts = data.get("ts") or data.get("timestamp", "")
        if isinstance(ts, str):
            from datetime import datetime
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age = (datetime.now(t.tzinfo) - t).total_seconds()
        else:
            age = time.time() - float(ts)
        return age <= stale_sec, data
    except Exception:
        return False, {}


def main() -> int:
    # ── NewAPI 与网关 ─────────────────────────────────────────────
    st = http_status("http://127.0.0.1:3002/api/status")
    check("newapi 3002", st == 200, f"HTTP {st}")
    check("ttft-gateway 3003", port_open(3003), "端口探测")
    check("cc-switch 15721", port_open(15721), "OMP 主链路端口")

    # ── 本地代理群 ────────────────────────────────────────────────
    proxies = {
        "agentrouter 8788": (8788, "100.83.32.95"),
        "codebuddy 8787": (8787, "100.83.32.95"),
        "anyrouter 8789": (8789, "127.0.0.1"),
        "codex-relay 15999": (15999, "127.0.0.1"),
        "codex-relay 16000": (16000, "127.0.0.1"),
    }
    for name, (p, h) in proxies.items():
        check(name, port_open(p, h), f"{h}:{p}")

    # ── guardian / supervisor / watchdog ───────────────────────────
    g_fresh, g_data = fresh_json(GUARDIAN / "heartbeat.json", 180)
    check("guardian 心跳", g_fresh, f"pid={g_data.get('pid')} ts={g_data.get('ts')}")
    g_proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Get-CimInstance Win32_Process -Filter \"ProcessId={g_data.get('pid', 0)}\" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name"],
        capture_output=True, text=True, timeout=15).stdout.strip()
    check("guardian 进程存活", bool(g_proc) and g_proc.lower().startswith("pythonw"), f"name={g_proc or 'none'}")

    s_fresh, s_data = fresh_json(GUARDIAN / "supervisor-status.json", 180)
    services = s_data.get("services", {})
    bad = [k for k, v in services.items() if not v.get("healthy")]
    check("supervisor 状态", s_fresh and not bad, f"all_ok={s_fresh and not bad} bad={bad or '无'}")

    crash = GUARDIAN / "watchdog-crash.log"
    crash_size = crash.stat().st_size if crash.exists() else 0
    check("watchdog 崩溃记录", crash_size == 0, f"{crash_size}B（应空）")

    # ── NewAPI 渠道健康（guardian metrics 快照）────────────────────
    m_fresh, m_data = fresh_json(GUARDIAN / "metrics.json", 600)
    ch = m_data.get("channels", {})
    check("渠道健康快照", m_fresh, f"total={ch.get('total')} healthy={ch.get('healthy')} disabled={ch.get('disabled')}")
    if m_fresh and ch.get("healthy", 0) < 10:
        check("健康渠道数量", False, f"healthy={ch.get('healthy')} < 10")

    # ── 资源与日志 ────────────────────────────────────────────────
    def dir_size(p: Path) -> int:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0
    omp_mb = dir_size(OMP) / 1048576
    check("~/.omp 体积", omp_mb < 5120, f"{omp_mb:.0f}MB（阈值 5GB）")
    for log in [GUARDIAN / "guardian.log", GUARDIAN / "watchdog.log", GUARDIAN / "proxies-supervisor.log"]:
        sz = log.stat().st_size if log.exists() else 0
        check(f"{log.name}", sz < 8 * 1048576, f"{sz // 1024}KB（阈值 8MB）")

    # ── NewAPI 备份新鲜度与 DB 体积 ───────────────────────────────
    # 检查最近 24h 内有备份（凌晨 0-3 点"今日备份"尚未生成，按日判断会误报）
    backups = sorted(NEWAPI_LOCAL.glob("backups/new-api-*.db"), key=lambda p: p.stat().st_mtime)
    cutoff = time.time() - 24 * 3600
    recent_bak = any(p.stat().st_mtime >= cutoff for p in backups)
    check("NewAPI 备份新鲜度", recent_bak, f"最近: {backups[-1].name if backups else '无'}")
    db_size_mb = 0
    for db in NEWAPI_LOCAL.glob("*.db"):
        db_size_mb += db.stat().st_size / 1048576
    check("NewAPI DB 体积", db_size_mb < 2048, f"{db_size_mb:.0f}MB（阈值 2GB）")

    # ── OMP 会话数据基线（观察项，阈值 5GB）────────────────────────
    sess_mb = dir_size(OMP / "agent" / "sessions") / 1048576
    check("OMP sessions 体积", sess_mb < 5120, f"{sess_mb:.0f}MB（阈值 5GB）")

    # ── 汇总 ──────────────────────────────────────────────────────
    failed = [r for r in RESULT if not r["ok"]]
    # 结果追加到健康日志（脚本内写文件，避免计划任务重定向引号问题）
    try:
        with open(GUARDIAN / "health-check.log", "a", encoding="utf-8") as f:
            f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            for r in RESULT:
                f.write(f"[{'OK ' if r['ok'] else 'FAIL'}] {r['name']}" + (f" — {r['detail']}\n" if r['detail'] else "\n"))
            f.write(f"summary: {len(RESULT) - len(failed)}/{len(RESULT)} OK\n")
    except OSError:
        pass
    if "--json" in sys.argv:
        print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "total": len(RESULT), "failed": len(failed), "results": RESULT}, ensure_ascii=False))
    print(f"\nsummary: {len(RESULT) - len(failed)}/{len(RESULT)} OK" + (" — ALL GREEN" if not failed else f" — {len(failed)} FAILED"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
