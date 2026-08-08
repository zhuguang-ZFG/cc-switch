# 系统成熟度报告：OMP × NewAPI × 本地代理（2026-08-08）

**Status:** MATURE —— 所有自愈路径实测通过，全量验证套件全绿
**Scope:** `~/.omp/`（agent/guardian）、`~/.new-api-local/`、NewAPI 3002、本地代理群
**验证时间:** 2026-08-08 13:34（最终全量回归）

## 1. 系统架构（当前状态）

```
┌────────────────────────── OMP（~/.omp）──────────────────────────┐
│ config.yml: 10 角色全解析，fallback 链无重复                        │
│ 直连 NewAPI 3002（zg-newapi）/ 3003 TTFT 网关（anthropic）          │
│ 角色: slow/plan/vision=opus-5 | task=deepseek | commit=haiku(agnes) │
│       tiny=hy3(WorkBuddy) | smol=sensenova | designer=gpt-5.6-sol   │
└──────────────┬──────────────────────────────────────────┬──────────┘
               │ 15721（Claude Code 主链路）                │ 3002/3003
┌──────────────▼──────────────┐        ┌──────────────────▼──────────┐
│ cc-switch 代理（supervisor  │        │ NewAPI（LocalNewAPI-Watchdog │
│ 监管，崩溃 30s 自愈）        │        │ 每分钟探活，崩溃 <90s 自愈） │
└──────────────┬──────────────┘        └──────────┬───────────────────┘
               │                                  │ 32 渠道（17 健康）
┌──────────────▼──────────────────────────────────▼───────────────────┐
│ 上游渠道池：opus-5×6 / haiku-agnes×2 / gpt-5.6-sol×多 / kimi / 其他    │
│ guardian：15s 健康检查 + 渠道自愈 + 权重闭环 + Telegram 告警            │
└──────────────────────────────────────────────────────────────────────┘
```

## 2. 守护体系（三层，全部实测）

| 守护 | 进程 | 职责 | 监管者 | 崩溃恢复（实测） |
|------|------|------|--------|------------------|
| Guardian | pythonw 33568 | 渠道自愈/权重/告警/NewAPI 重启 | Watchdog | ✅ ~3.5min（180s 心跳过期+拉起） |
| Watchdog | powershell | 守护 guardian + supervisor | 计划任务（30s 循环+崩溃留痕+轮转） | ✅ 10min 观察稳定 |
| Supervisor | python 25624 | 7 本地服务端口探测+拉起 | Watchdog | ✅ 30s |
| LocalNewAPI-Watchdog | powershell | NewAPI 3002 探活+拉起 | 计划任务（每分钟） | ✅ <90s |

**supervisor 监管 7 服务**：cc-switch-proxy(15721)、omp-ttft(3003)、codex-relay-15999、codex-relay-16000、agentrouter(8788)、codebuddy(8787)、anyrouter(8789)

## 3. 验证矩阵（13:34 全绿）

```text
newapi-local-smoke:   ALL OK
test_guardian.py:     95/95 OK
test_omp_routes.py:   32/32 OK
test_codex_relay.py:  14/14 OK
test_omp_ttft_gateway.cjs: 5/5 OK
主链路 smoke（15721→NewAPI→上游）: 200
```

## 4. 关键运维知识（防回归）

1. **PS 5.1 + 无 BOM UTF-8 中文注释 = 吞行**：修改含中文 .ps1 后必须重加 BOM（`WriteAllText` + `UTF8Encoding($true)`）+ Parser 验证。edit 工具每次重写会移除 BOM。
2. **PowerShell 查询自匹配**：`-match 'watchdog.ps1'` 匹配查询命令自己；禁止无差别 kill；验证存活用 mutex 探测（probe 报 already running）。
3. **supervisor 拉 exe 需绝对路径**（相对路径 WinError 2）；**relay 探活必须 probe_host=127.0.0.1**；**pythonw 在 PowerShell 启动环境即退**，用 python.exe。
4. **guardian.py 修改需同步两处**：运行时 `~/.omp/guardian/guardian.py` + 仓库镜像 `scripts/ops/guardian.py`（曾漂移，已修复）。
5. **OMP 配置重启生效**：config.yml/models.yml/agent frontmatter 改动需 OMP 重启；OMP 启动重选可能覆盖 default（重启后需核对）。

## 5. 剩余风险（已知且接受，无需紧急动作）

| 风险 | 等级 | 说明 | 缓解/触发条件 |
|------|------|------|---------------|
| ch73/74 relay 渠道禁用 | 低 | zzzcoding/sharedchat 上游 405 真死；已在 guardian/smoke 排除集 | 上游恢复后手动启用 + 移出排除集 |
| NewAPI 无模型级限流 | 低 | `ModelRequestRateLimitEnabled=false`，公告 120/min 未落地；OMP 应用层并发已限 | 出现渠道 429 风暴时评估开启 |
| commit/tiny 角色未实测触发 | 低 | 路由解析已验证（32/32），真实链路待首次使用 | 首次触发后查 consume log（commit→agnes-2.0-flash） |
| relay 计划任务双实例 | 已收敛 | 两个 LogonTrigger 任务已 Disable，supervisor 单一管理 | 如需回滚 Enable 即可 |
| ~/.omp 3.2G 数据增长 | 观察 | sessions 1.1G + plugins 919M + 浏览器 profile 398M | 季度检查；日志已全部轮转 |
| 3002 局域网暴露 | 已收口 | 防火墙 LocalSubnet 仅本子网，Tailscale 其他节点不可达（/32） | 需跨节点访问时加 100.64.0.0/10 规则 |

## 6. 今日变更总账（7 commit：75dadc5a → 4781db18）

- **自愈补齐**：relay 纳入 supervisor、cc-switch 纳入 supervisor、watchdog 看护 supervisor、崩溃留痕+循环不退出
- **深坑修复**：BOM 吞行×2、pythonw 启动即退、exe 相对路径、probe_host、查询自匹配误杀
- **配置成熟**：haiku(commit)/hy3(tiny) 角色上岗、reviewer 还原 @slow、designer 链去重、模型定价补齐（49/49）
- **资产同步**：guardian 测试 95/95、smoke 排除集、仓库镜像同步、验证套件全绿
- **运维固化**：日志轮转（watchdog/guardian）、备份 drill 月度任务、user_sessions 每日清理、NewAPI 每日备份

## 7. 监控建议（日常巡检）

```powershell
# 三守护心跳（30 秒内新鲜即可）
Get-Content ~/.omp/guardian/heartbeat.json          # guardian
Get-Content ~/.omp/guardian/supervisor-status.json  # supervisor（7 服务 all_ok）
Get-Content ~/.omp/guardian/watchdog-crash.log      # watchdog 崩溃记录（应为空）
# 全量验证（分钟级）
python scripts/ops/newapi-local-smoke.py
```
