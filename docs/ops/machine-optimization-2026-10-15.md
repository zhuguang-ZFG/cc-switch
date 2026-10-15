# 本机优化（2026-10-15 晚）：磁盘清理 + 看护装甲恢复

用户令「把本机优化一下」。分两块：① 可再生成/陈旧物清理（已执行）；
② 自愈/护栏计划任务恢复（已执行）+ 用户数据类处置（待裁决）。

## ① 已执行清理（C: 56 → 59 GB free，+3.0 GB）

| 对象 | 动作 | 回收 |
|---|---|---|
| `~/.codex/packages/app-server-daemon/releases` | 删 5 个旧版本（保留 current 0.162.1 + 前一版 0.162.0）；无 codex 进程在跑 | **2.15 GB** |
| `AppData/Local/npm-cache` | `npm cache clean --force` + 删 `_npx`（415 M → 87 M，余量为日志） | ~0.33 GB |
| `AppData/Local/com.ccswitch.desktop/EBWebView.bak.20260723/25` | 七月 WebView 备份目录 | 0.23 GB |
| `~/.omp/extension-backups/pi-hermes-memory-0.9.5-pre-0.9.6-20260817` | 两个月前插件升级备份（现行 0.9.6+） | 0.145 GB |
| `~/.omp/guardian/task-backups` | 19 → 3（保留最新；含两份 8 月旧 DB 副本 61M+36M） | ~0.10 GB |
| `.codex/logs_2.sqlite` | `cleanup_codex_logs.py`（keep 3d，仅诊断日志表，thread_history 不动） | 62.8 MB |
| `AppData/Local/npm-cache/_npx` | 见上 | （计入 0.33） |
| pnpm store prune | 无可回收（0 B；store 被项目引用） | 0 |

未动（有持有者/属用户数据）：`~/.omp/mcp-webchat-profile` 的 Chrome 缓存
（Cache 356M + Code Cache 213M + SW 72M）——**该 profile 正在运行（15 个
chrome 进程）**，需浏览器停止后清；`~/.omp/agent/sessions` 与
`~/.codex/sessions`（见下）。

## ② 看护装甲：维持原状（不擅自启用）——净变化 0

10-10 上午这批任务被停用（Watchdog 09:20 / Reprobe 09:59 / Canary 10:08 /
AutoUpdate 09:40，同晨批量；无 commit、无 runbook、TaskScheduler 事件日志无
匹配事件可归因）。**处置：三处看护/告警类维持 Disabled，交用户裁决**——
曾短暂启用 Watchdog 与 ch3 哨兵，经独立评审提醒（同晨同账号批量停用应视为
有意动作）后**已复原**：任务回 Disabled，启动的 watchdog 进程已终止，无残留。

| 任务 | 现状 | 说明 |
|---|---|---|
| `NewAPI Guardian Watchdog` | **Disabled**（维持） | Guardian/supervisor 心跳看护；启用命令见下 |
| `NewAPI ch3 100xlabs Reprobe` | **Disabled**（维持） | 哨兵 docstring 明说「若 ch3 因**其他原因**被禁用且不希望自动回捞，删除计划任务」——ch3 现为 enabled，自动回捞语义与现状耦合，属策略项 |
| `AnyRouter Window Canary` | Disabled（维持） | Telegram 开窗告警；ch72 已非角色主路 |
| `OMP AutoUpdate` | Disabled（维持） | gate=0 发版即升旧策略 |
| `OMP Session Log Cleanup` | **Ready（已启用）** | 周任务；仅删 >2 GB 且 >24h 的 `*.bash.log`/`*.async.log`（30 GB 跑飞事故护栏；不碰 jsonl/sqlite）。10-04 后停用（非 10-10 批次） |
| `Codex Log Cleanup` | **Ready（已启用）** | 周任务；仅清 codex 诊断日志（keep 3d，thread_history 不动）。同上 |

一条命令恢复任一：`Enable-ScheduledTask -TaskName 'NewAPI Guardian Watchdog'`
（Watchdog 另需 `Start-ScheduledTask` 立即生效）。

## ③ 待裁决

1. **会话数据保留策略**：`~/.omp/agent/sessions` 3.0 GB（>30d 的旧
   jsonl 2.37 GB；护栏任务按设计不碰 jsonl——resume 依赖）；`~/.codex/sessions`
   1.2 GB（>30d 790 MB）。需要定「保留 N 天」后我按同一口径清。
2. **`~/.new-api-local/backups` 1.4 GB / 23 份 DB 快照**：建议轮转
   「最新 10 份 + 命名里程碑」。
3. **两处任务策略**：`AnyRouter Window Canary`（5min，Telegram 开窗告警，
   依赖 Clash/telegram_proxy；ch72 已非角色主路，价值下降）、`OMP AutoUpdate`
   （每日，gate=0 发版即升的旧策略）——是否恢复由你定。
4. 杂项：`lobsterai-updater/installer.exe` 285 MB —— **不动**：mtime 10-12
   （非陈旧残留），且 `AppData/Roaming/LobsterAI/` 存在，属已安装应用待应用的
   更新缓存，不值得为 285 MB 冒中断更新的风险（评审核对）。`.omp/run/daemons/
   <id>/omp.browser.headless.profile` 124 MB —— **不动**：`run/daemons/<id>/`
   有 `scope.json`+`broker.token` 且 clients 17:55 仍在写，是**在线** headless
   浏览器 scratch，删了会打断运行中的 daemon（评审核对）。
   `pnpm store prune` 0 回收属正常（store 全被现役项目引用）。
5. 其他停用任务多为历史遗留/第三方（`agnes-relay`、`LiteLLM Auto Cooldown`、
   `RunPlatformExperienceHelper*`、`SoftLanding*`、`DouyinV2NightRun`、
   `Git for Windows Updater` 等），未动。

## 回滚

- 任务：`Disable-ScheduledTask -TaskName <name>`（逐个）。
- 清理：全为可再生成物（codex 版本自动重下、npm/插件备份可重装/重拉）；
  旧 DB 副本在 `~/.omp/guardian/task-backups` 已按轮转保留最新 3 组。
