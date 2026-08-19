#!/usr/bin/env python3
"""system-health-check — 一键巡检 OMP × NewAPI × 本地代理全链路。

覆盖：NewAPI/TTFT/cc-switch/7 本地代理端口、guardian 心跳、supervisor 状态、
watchdog 崩溃记录、new-api 进程存活、看门狗计划任务上次结果/新鲜度
（0x800710E0 挂起类）、start.ps1 BOM/-Wait 完整性、watchdog.ps1 ASCII 契约、
NewAPI 渠道健康、关键日志大小、磁盘余量。
退出码：0 = 全绿；1 = 有失败项（供计划任务/告警判断）。
用法：python scripts/ops/system-health-check.py [--json]
"""
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HOME = Path.home()
OMP = HOME / ".omp"
GUARDIAN = OMP / "guardian"
NEWAPI_LOCAL = HOME / ".new-api-local"
RESULT: list[dict] = []
DX_SMOKE_TASK = "CCSwitch-NewAPI-DX-Ops"
DX_SMOKE_STALE_SEC = 5 * 60 * 60


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


def scheduled_task_status(
    raw: str, stale_sec: int, now: datetime | None = None
) -> tuple[bool, str]:
    """Validate one Task Scheduler result encoded as result|time|state."""
    if not raw or raw == "missing":
        return False, f"task missing or unreadable: {raw!r}"
    try:
        result_s, run_s, state = (raw.split("|") + ["", ""])[:3]
        result = int(result_s) & 0xFFFFFFFF
        run_time = datetime.fromisoformat(run_s)
        current = now or datetime.now(run_time.tzinfo)
        age = (current - run_time).total_seconds()
    except (TypeError, ValueError):
        return False, f"invalid task status: {raw!r}"
    ok = result == 0 and -60 <= age < stale_sec and state in {"Ready", "Running"}
    detail = (
        f"result=0x{result:08X} last_run={run_s} state={state} age_sec={int(age)}"
    )
    if result == 0x800710E0:
        detail += " (IgnoreNew rejected trigger; a previous task instance is stuck)"
    return ok, detail


def query_scheduled_task(task_name: str, stale_sec: int) -> tuple[bool, str]:
    escaped = task_name.replace("'", "''")
    command = (
        f"$t = Get-ScheduledTask -TaskName '{escaped}' -ErrorAction SilentlyContinue; "
        f"$i = Get-ScheduledTaskInfo -TaskName '{escaped}' -ErrorAction SilentlyContinue; "
        "if ($t -and $i) { '{0}|{1}|{2}' -f $i.LastTaskResult, "
        "$i.LastRunTime.ToString('o'), $t.State } else { 'missing' }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if result.returncode != 0:
            return False, f"query failed rc={result.returncode}"
        return scheduled_task_status(result.stdout.strip(), stale_sec)
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"query error: {type(error).__name__}: {error}"


def relay_owner_violations(processes: object, listeners: object) -> list[str]:
    """Require one codex-relay process and matching listener per relay port."""
    if not isinstance(processes, list) or not isinstance(listeners, list):
        return ["relay ownership query returned invalid shape"]
    violations: list[str] = []
    for port in (15999, 16000):
        matches = []
        for process in processes:
            if not isinstance(process, dict):
                continue
            command = str(process.get("CommandLine") or "")
            if (
                re.search(r"(?i)(?:^|[\\/])codex-relay\.py(?:\s|$)", command)
                and re.search(rf"(?:^|\s)--port(?:=|\s+){port}(?:\s|$)", command)
            ):
                matches.append(process)
        if len(matches) != 1:
            violations.append(f"port {port}: relay_processes={len(matches)} expected=1")
            continue
        try:
            process_id = int(matches[0]["ProcessId"])
        except (KeyError, TypeError, ValueError):
            violations.append(f"port {port}: relay process has invalid pid")
            continue
        listener_pids = {
            int(listener["OwningProcess"])
            for listener in listeners
            if isinstance(listener, dict)
            and listener.get("LocalPort") == port
            and listener.get("OwningProcess") is not None
        }
        if listener_pids != {process_id}:
            violations.append(
                f"port {port}: process_pid={process_id} listener_pids="
                f"{sorted(listener_pids)}"
            )
    return violations


def query_relay_owner_violations() -> list[str]:
    """Read Windows relay processes/listeners without exposing command lines."""
    command = (
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
        "$p=@(Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -match 'codex-relay\\.py' } | "
        "Select-Object ProcessId,CommandLine); "
        "$l=@(Get-NetTCPConnection -State Listen -LocalPort 15999,16000 "
        "-ErrorAction SilentlyContinue | Select-Object LocalPort,OwningProcess); "
        "@{processes=$p;listeners=$l} | ConvertTo-Json -Depth 4 -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if result.returncode != 0:
            return [f"relay ownership query failed rc={result.returncode}"]
        payload = json.loads(result.stdout)
        processes = payload.get("processes", [])
        listeners = payload.get("listeners", [])
        if isinstance(processes, dict):
            processes = [processes]
        if isinstance(listeners, dict):
            listeners = [listeners]
        return relay_owner_violations(processes, listeners)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError) as e:
        return [f"relay ownership query error: {type(e).__name__}: {e}"]


def main() -> int:
    # ── NewAPI 与网关 ─────────────────────────────────────────────
    st = http_status("http://127.0.0.1:3002/api/status")
    check("newapi 3002", st == 200, f"HTTP {st}")
    check("ttft-gateway 3003", port_open(3003), "端口探测")
    check("cc-switch 15721", port_open(15721), "OMP 主链路端口")

    # ── 本地代理群 ────────────────────────────────────────────────
    proxies = {
        "agentrouter 8788": (8788, "100.83.32.95"),
        "anyrouter 8789": (8789, "127.0.0.1"),
        "codex-relay 15999": (15999, "127.0.0.1"),
        "codex-relay 16000": (16000, "127.0.0.1"),
        "mistral-relay 16001": (16001, "127.0.0.1"),
    }
    for name, (p, h) in proxies.items():
        check(name, port_open(p, h), f"{h}:{p}")
    relay_violations = query_relay_owner_violations()
    check(
        "codex relay 单实例归属",
        not relay_violations,
        f"violations={relay_violations or 'none'}",
    )

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

    # ── new-api 进程与看门狗任务活性（2026-08-10 事故类）────────────
    # 端口存活不等于进程健康；进程存在但端口死 = 半死状态，两者都查。
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq new-api.exe", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=15)
        check("new-api.exe 进程存活", "new-api.exe" in proc.stdout,
              "tasklist 探测")
    except Exception as e:  # noqa: BLE001
        check("new-api.exe 进程存活", False, f"tasklist error: {e}")
    # 计划任务"已启用"≠能跑：MultipleInstancesPolicy=IgnoreNew 下，挂起实例会以
    # 0x800710E0 拒绝每次新触发（start.ps1 曾被注入 -Wait 导致）。校验上次结果与新鲜度。
    watchdog_ok, watchdog_detail = query_scheduled_task(
        "LocalNewAPI-Watchdog", 600
    )
    check("看门狗计划任务", watchdog_ok, watchdog_detail)
    smoke_ok, smoke_detail = query_scheduled_task(
        DX_SMOKE_TASK, DX_SMOKE_STALE_SEC
    )
    check("NewAPI 综合 smoke 计划任务", smoke_ok, smoke_detail)
    # start.ps1 完整性：PS 5.1 对无 BOM 脚本按 ANSI 解析（中文注释字节错位→崩溃），
    # -Wait 会让看门狗同步挂起（两者均为 2026-08-10 凌晨事故根因，防回归）。
    try:
        raw = (NEWAPI_LOCAL / "start.ps1").read_bytes()
        has_bom = raw.startswith(b"\xef\xbb\xbf")
        import re
        wait_on_start = bool(re.search(rb"Start-Process[^\n]*-Wait", raw))
        check("start.ps1 完整性", has_bom and not wait_on_start,
              f"BOM={has_bom} Start-Process-Wait={wait_on_start}")
    except OSError as e:
        check("start.ps1 完整性", False, f"read error: {e}")
    # watchdog.ps1 契约：必须 ASCII-only（文件头注释声明；非 ASCII + 无 BOM = PS 5.1 解析错位）。
    try:
        wd = (NEWAPI_LOCAL / "watchdog.ps1").read_bytes()
        ascii_only = all(b < 128 for b in wd)
        check("watchdog.ps1 ASCII 契约", ascii_only,
              "ascii" if ascii_only else "发现非 ASCII 字节（需补 UTF-8 BOM 或改回 ASCII）")
    except OSError as e:
        check("watchdog.ps1 ASCII 契约", False, f"read error: {e}")

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
