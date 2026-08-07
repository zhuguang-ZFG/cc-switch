# SharedChat Codex Sol 接入 NewAPI（2026-08-07）

**状态：** 已登记并隔离，等待上游额度恢复；尚未加入可用生产池
**范围：** SharedChat Codex、NewAPI channel 74、本地 Codex relay、OMP `zg-newapi/gpt-5.6-sol`

## 结论

SharedChat 渠道已经创建并完成本地接线，但当前上游返回
`global_fixed_window_quota_exhausted`。为避免 Sol 聚合首跳稳定失败，channel 74
保持手动禁用（`status=2`）。这不是“已经可用”的聚合接入，额度恢复并通过完整验收前不得启用。

用户提供的 API key 仅保存在 NewAPI channel 74 和本机 Guardian 外部 secret store；
仓库、文档、日志和测试输出均不保存密钥值。

## 当前拓扑

```text
OMP zg-newapi/gpt-5.6-sol
  -> NewAPI 127.0.0.1:3002
  -> channel 74 (当前禁用)
  -> relay 127.0.0.1:16000
  -> https://new.sharedchat.cc/codex/v1/responses
```

本地 relay 负责把 OpenAI Chat Completions 请求转换为 Responses 请求，并补齐当前
Codex CLI 指纹。NewAPI 不再对 channel 74 进行全局 Chat-to-Responses 转换。

## 渠道配置

| 字段 | 值 |
| --- | --- |
| ID / 名称 | `74` / `sharedchat-codex-sol` |
| 类型 | OpenAI-compatible（type 1） |
| Base URL | `http://127.0.0.1:16000` |
| Models | `gpt-5.6-sol,zg-gpt-5.6-sol` |
| Mapping | `zg-gpt-5.6-sol -> gpt-5.6-sol` |
| Test model | `gpt-5.6-sol` |
| Priority / Weight | `50 / 5` |
| Auto ban | `1` |
| Status | `2`（手动禁用） |
| Header override | 空 |

`abilities` 中已生成两条模型记录，并与渠道的 priority/weight 对齐；渠道禁用时它们
不会进入可用池。全局转换策略已恢复为变更前配置，channel 74 不在转换列表中。

## Relay 与任务

- 仓库实现：`scripts/ops/codex-relay.py`
- 注册脚本：`scripts/ops/register-codex-relay-task.ps1`
- 生产副本：`~/.omp/guardian/codex-relay-15999/` 与 `~/.omp/guardian/codex-relay-16000/`，每个任务拥有独立脚本和模板
- SharedChat 任务：`OMP SharedChat Codex Relay`，端口 `16000`
- 原 zzzcoding 任务：`OMP Codex Relay`，端口 `15999`
- 两个任务均使用 `pythonw.exe`，不会弹出控制台窗口。
- 两个任务均使用 `IgnoreNew`、Limited、battery-safe、`3 x PT1M` 有界重启。

relay 支持独立的 `--upstream`、`--secret-name` 和 `--log-file`，两个渠道共享同一份
经过测试的仓库实现，但使用不同生产目录、端口、上游、secret 名称和日志。注册流程
先完成 URL、Python 语法和模板 JSON 校验，再备份任务 XML 与旧运行时；启动后必须
验证监听 PID 的解释器、脚本路径和端口参数，失败时恢复旧任务。上游 URL 必须是无
userinfo 的 HTTPS URL，secret 名称只允许字母、数字和下划线。自定义 secret 使用
作用域环境变量 `CODEX_RELAY_KEY_<SECRET_NAME>`；通用 `CODEX_RELAY_KEY` 只兼容默认
zzzcoding secret，不能覆盖 SharedChat 的命名 secret。

## 假健康与真实阻塞

SharedChat `/v1/chat/completions` 可返回 HTTP 200，但正文说明旧转发链路已关闭，
因此不能作为成功证据。真实 Codex CLI 请求与补齐指纹后的 relay 请求均能通过客户端
限制检查，但当前 `/v1/responses` 返回 HTTP 403：

```text
type=rate_limit_error
code=global_fixed_window_quota_exhausted
```

2026-08-07 19:15（Asia/Shanghai）再次通过 `127.0.0.1:16000` 发起主动非流式
请求，仍得到上述 403。失败位于 SharedChat 上游额度层，不是 relay 启动或端口故障。

## 验证记录

- relay 单元测试：12/12 通过（参数、secret 选择、HTTPS 校验、Codex 0.146 指纹、无窗口任务等）。
- relay、Guardian、smoke、OMP route 完整相关回归共 168/168 通过；两个 relay 在测试前后保持同一 PID。
- PowerShell 注册脚本 AST 解析为 0 errors。
- `127.0.0.1:15999` 与 `127.0.0.1:16000` 均由独立 `pythonw.exe` 任务监听。
- NewAPI `127.0.0.1:3002` 正常监听；channel 74 仍由 intentional-disable gate 保护。
- Guardian 的 `AUTO_BAN_RECOVERY_EXCLUSIONS` 包含 74；即使渠道进入 `status=3`，也不会绕过人工验收自动恢复。
- live smoke 中 `3002`、`8787/8788/8789`、`15999/16000` 均通过，两条真实低成本 completion 均返回 HTTP 200；唯一失败是本次变更前已存在的 channel 70 unexpected auto-disabled。
- 隔离目录迁移后，15999 的 `gpt-5.5` 真实非流式 marker 返回 HTTP 200；16000 返回预期的 `global_fixed_window_quota_exhausted`，证明两个任务使用各自的运行时、secret 选择和上游。

不能从这些局部检查声称端到端成功。当前缺少的生产验收全部依赖上游额度恢复。

## 启用门槛

额度恢复后按以下顺序执行，任一步失败都保持 channel 74 禁用：

1. relay 非流式 marker 返回真实 assistant 文本。
2. relay SSE 返回 marker、`text/event-stream` 和且仅一个 `[DONE]`。
3. relay function call 返回 OpenAI `tool_calls` 语义。
4. NewAPI `/api/channel/test/74` 的业务结果成功，而不只是 HTTP 200。
5. 启用 channel 74，确认 abilities 生效且 priority/weight 为 `50/5`。
6. 请求聚合模型 `zg-gpt-5.6-sol`，NewAPI 日志明确记录 `channel_id=74`。
7. 使用 OMP 请求 `zg-newapi/gpt-5.6-sol`，核对 assistant/model/provider，并再次确认 NewAPI 实际命中 74。
8. 检查 Guardian 未 auto-ban、恢复队列无异常；从 `KNOWN_BROKEN_CHANNELS` 移除 74，重跑完整 unit/live smoke。

## 回滚

变更前备份位于：

```text
C:\Users\zhugu\.omp\guardian\task-backups\sharedchat-sol-20260807-174716
```

审查修复前的 relay/Guardian/任务 XML 补充备份位于：

```text
C:\Users\zhugu\.omp\guardian\task-backups\sharedchat-review-fixes-20260807-195000
```

其中 `new-api.before-sharedchat-sol.db` 是 SQLite 在线备份，创建后
`quick_check=ok`；`relay-runtime-before` 保存了 relay、模板、计划任务 XML 和 secret
文件的变更前副本。恢复前必须再次核对备份存在、大小、时间和内容边界。

优先回滚步骤是保持 channel 74 禁用并停止 `OMP SharedChat Codex Relay`；只有需要撤销
数据库登记时才使用 SQLite 备份。不要停止独立的 channel 73 relay，也不要修改
CC Switch 数据库、schema 或二进制。
