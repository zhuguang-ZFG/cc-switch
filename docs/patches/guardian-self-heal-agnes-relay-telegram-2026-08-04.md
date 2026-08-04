# Guardian 自愈修复 + agnes-relay 弹窗 + Telegram 告警治理 (2026-08-04)

## 背景

一系列 ops 问题：Guardian 本地代理探针误报、Telegram 告警每分钟刷屏、agnes-relay 触发 360 "隐藏执行 PowerShell" 弹窗、CatPaw Bridge 接入后当日因能力不足移除。

## 修复清单

### 1. Guardian 本地代理探针禁用（P0）

**症状**：`check_local_proxy` 对 agentrouter/codebuddy/atomcode 发 `/v1/models` 请求，代理超时/失败时发送"本地代理推理异常"告警——用户确认"没啥用"。

**修复**：`HealthChecker.check_local_proxy` 直接返回 `(True, f"{name} 探针已禁用", True)`，不再真正发请求。`LOCAL_PROXIES` 定义不变（agentrouter:8788, codebuddy:8787, atomcode:9457），只是探针跳过。

### 2. Guardian Telegram 告警频率治理（P0）

**症状**：代理推理超时每分钟触发一次 Telegram 告警（atomcode/agentrouter/codebuddy 在 21:02-21:03 四连告警）。

**修复**：`_check_cycle` 中增加 `self.inference_alerted` 事件去重字典（keyed by proxy name），同一代理连续失败只发一次告警，恢复后清除。对应 `restart_alerted` 断路器模式。新增回归测试 `test_inference_alert_sent_once_per_episode`（98/98 通过）。

### 3. Guardian 渠道测试超时放宽（P1）

**症状**：`TEST_CHANNEL_TIMEOUT = 5` 秒，但上游（ch45 agentrouter avg 30s+, ch55 inferx avg 46.8s）远超此值，导致"渠道测试超时"误报。

**修复**：`TEST_CHANNEL_TIMEOUT: 5 → 15`，与本地代理探针超时（8→15）一致。

### 4. agnes-relay 360 弹窗修复（P0）

**症状**：`\agnes-relay` 计划任务使用 `powershell.exe -WindowStyle Hidden` 运行 `run-agnes-relay.ps1`，360 安全卫士拦截"隐藏执行 PowerShell"并弹窗。任务原带 `TimeTrigger PT1M`（每分钟触发），21:35:37 弹窗复现。

**修复**：
- 创建 `run-agnes-relay.py`（Python supervisor），替换原 `run-agnes-relay.ps1`
- 任务命令从 `powershell.exe ...` 改为 `python.exe run-agnes-relay.py`
- 重建任务 XML，移除 `TimeTrigger PT1M`，仅保留 `LogonTrigger`
- `MultipleInstancesPolicy: IgnoreNew`，`RestartOnFailure` 保留（999 次/1 分钟间隔）

**验证**：勾子监听 `100.83.32.95:9460 -> apihub.agnes-ai.com`，任务模式"正在运行"，计划类型"登陆时"，重复 N/A。

### 5. CatPaw Bridge 完整移除

**背景**：2026-08-04 当日接入 CatPaw Bridge（Windows CatPaw 实时会话凭据 + Tailscale 100.83.32.95:4567 Bridge + NewAPI ch71 + OMP 6 个 `catpaw-*` 模型）。实测 REST 端点有效上下文 ≈13k tokens（单条超限返回 `code 9999 内容长度异常`；多轮超 ~13k 被服务端压缩到 ~10k），且无思维链强度参数（thinking/reasoning_budget 不生效，reasoning_tokens 总为 0）。用户选择完整移除。

**移除动作**：
- Bridge 目录删除、watchdog 进程停止（PID 18920 + 11228/27444，二次清除）
- Startup 启动行移除（`ai-proxy-resilience.cmd` 删 `CatPaw Bridge Watchdog` 行）
- NewAPI ch71 删除（`DELETE /api/channel/71`，200 "record not found" 确认）
- OMP models.yml 6 个 catpaw 条目删除（sed 58,87d，验证零 catpaw 引用）
- `secrets.json` 中 `catpaw_bridge_api_key` 删除（8 keys 剩余）
- 所有 catpaw 备份文件、tmp 引用、应用数据（`~/.meituan-catpaw`、`AppData\Roaming\catpaw-moon`、`AppData\Local\CatPaw`）全部清理
- 注册表、计划任务、启动项零 catpaw 引用

### 6. 死渠道删除

**删除渠道**：ch26, ch28, ch38, ch49, ch54（均 `DELETE /api/channel/{id}` 返回 "record not found" 确认删除）。ch49/ch54（inferx-glm52）此前因租户额度耗尽（`tenant quota exceeded`）被禁用。

### 7. PowerShell 计划任务清理（待管理员确认）

**删除目标**（被拒，需管理员权限）：
- `\CLIProxyMemWatchdog` → `D:\Users\grok-auto-register\scripts\cliproxy_mem_watchdog.ps1`
- `\K12StackWatchdog` → `D:\Users\grok-auto-register\scripts\k12_stack_watchdog.ps1`

当前状态：已禁用，不会触发弹窗。物理删除需在管理员 PowerShell 中运行 `schtasks /delete /f /tn "\<任务名>"`。

### 8. OMP 故障路由禁用

**修改**：`config.yml` `retry.modelFallback: true → false`

**效果**：失败时不再自动路由到 fallback 链上的其他模型，只重试原模型（最多 2 次）。`maxRetries: 2`、`baseDelayMs: 3000`、`maxDelayMs: 60000` 保留不变。

## 影响范围

- **Guardian**：`C:/Users/zhugu/.omp/guardian/guardian.py`（PID 28304 运行，心跳正常）
- **agnes-relay**：`C:/Users/zhugu/.omp/proxies/agnes-relay/run-agnes-relay.py` + `agnes-relay.xml`（仅 LogonTrigger）
- **OMP**：`C:/Users/zhugu/.omp/agent/config.yml`（modelFallback: false）
- **NewAPI**：ch26/28/38/49/54/71 已删除
- **CatPaw**：所有痕迹清除

## 验证

- Guardian 98/98 测试通过
- agnes-relay 监听 `100.83.32.95:9460` 正常
- 所有本地代理进程存活性确认（agentrouter/codebuddy/atomcode）
- Telegram 告警不再每分钟刷屏（inference_alerted 去重）
- OMP modelFallback 关闭