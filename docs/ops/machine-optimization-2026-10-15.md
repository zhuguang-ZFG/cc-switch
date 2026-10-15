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

## ② 看护/护栏任务恢复（四处 Disabled → Ready/已起）

10-10 上午这批任务被停用（无 commit/文档/事件日志可归因；ch3 哨兵当时的
最后几次运行是 401 失败，现 token 复测 HTTP 200 正常）。恢复：

| 任务 | 作用 | 现状 |
|---|---|---|
| `NewAPI Guardian Watchdog`（登录触发） | Guardian/代理 supervisor 心跳看护与重启（README 规范入口） | Ready，**已启动**（log: `18:36:59 Guardian watchdog started`，State=Running） |
| `NewAPI ch3 100xlabs Reprobe`（15min） | ch3 掉线后按 4/4 探针自动回捞（幂等：status==1 即退） | Ready，**18:36 实跑 result=0** ✓ |
| `OMP Session Log Cleanup`（周） | 仅删 >2 GB 且 >24h 的 `*.bash.log`/`*.async.log`（30 GB 跑飞事故护栏；不碰 jsonl/sqlite） | Ready |
| `Codex Log Cleanup`（周） | 仅清 codex 诊断日志（keep 3d） | Ready |

## ③ 待裁决

1. **会话数据保留策略**：`~/.omp/agent/sessions` 3.0 GB（>30d 的旧
   jsonl 2.37 GB；护栏任务按设计不碰 jsonl——resume 依赖）；`~/.codex/sessions`
   1.2 GB（>30d 790 MB）。需要定「保留 N 天」后我按同一口径清。
2. **`~/.new-api-local/backups` 1.4 GB / 23 份 DB 快照**：建议轮转
   「最新 10 份 + 命名里程碑」。
3. **两处任务策略**：`AnyRouter Window Canary`（5min，Telegram 开窗告警，
   依赖 Clash/telegram_proxy；ch72 已非角色主路，价值下降）、`OMP AutoUpdate`
   （每日，gate=0 发版即升的旧策略）——是否恢复由你定。
4. 杂项：`lobsterai-updater/installer.exe` 285 MB（10-12 的安装器，
   是否已装完可删）、`.omp/run/daemons/.../omp.browser.headless.profile`
   124 MB（头less浏览 profile）。
5. 其他停用任务多为历史遗留/第三方（`agnes-relay`、`LiteLLM Auto Cooldown`、
   `RunPlatformExperienceHelper*`、`SoftLanding*`、`DouyinV2NightRun`、
   `Git for Windows Updater` 等），未动。

## 回滚

- 任务：`Disable-ScheduledTask -TaskName <name>`（逐个）。
- 清理：全为可再生成物（codex 版本自动重下、npm/插件备份可重装/重拉）；
  旧 DB 副本在 `~/.omp/guardian/task-backups` 已按轮转保留最新 3 组。
