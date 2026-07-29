# gpt-5.6-sol「总是失败」诊断 + config thinking 误标修复 + 渠道 3 百倍健康核查

**Date:** 2026-07-29
**Status:** Done — 本机 `config.toml` 修 1 处（sol 去掉 thinking/effort 误标）；VPS/NewAPI 零改动（仅只读诊断）

## 1. gpt-5.6-sol「总是失败」根因

用户反馈 kimi 里调 `zg-newapi/gpt-5.6-sol` 总失败，疑似思维链设置问题。**不是 thinking 的锅。**

| 证据 | 结论 |
|---|---|
| NewAPI 容器近 30 分钟 sol 失败 125 条 | **全部** `status_code=500 sensitive_words_detected` |
| 命中渠道 | `2(ai.centos.hk-gpt)`、`16(centos-api-backup-gpt)`、`25(centos-api-newkey-gpt)` 全中（优先级 50，均启用） |
| NewAPI 本地敏感词检测 | `CheckSensitiveEnabled` / `CheckSensitiveOnPromptEnabled` / `StopOnSensitiveEnabled` **均 false** → 不是本机拦 |
| `status_code=500` | 上游 HTTP 响应 → 是 **provider 自己的内容审核**返回 |
| 无害短 prompt 冒烟（PONG / 读文件） | 成功（completion_tokens 6/11/23/76/262） |

判读：失败是**上游内容审核间歇性拦截**（真实干活的代码/系统提示/长上下文 prompt 触发 `sensitive_words_detected`），与 `reasoning_effort` 无关。四渠道轮完仍失败 → 用户侧表现为「总是失败」。

**复测（20:29–20:38）**：sol 已恢复正常，3 条记录全 `stream_status status:ok / end_reason:done`（渠道 2/16，prompt_tokens 46k–51k 真实体量），近 15 分钟无 `sensitive_words_detected`。这些成功请求仍带 `reasoning_effort:high` → 进一步排除 thinking 为失败主因。

## 2. config.toml thinking 误标修复（次要加重项）

`~/.kimi-code/config.toml` 的 `[models."zg-newapi/gpt-5.6-sol"]` 违反 AGENTS.md 铁律（gpt 全系不返回 `reasoning_content`，不该标 thinking）：

```diff
-capabilities = [ "thinking", "image_in" ]
-support_efforts = [ "low", "medium", "high", "xhigh" ]
-default_effort = "high"
+capabilities = [ "image_in" ]
```

现与 `gpt-5.5`（`capabilities = ["image_in"]`）对齐。误标会让 high-effort 请求长时间静默、偶发被中间代理判死连超时，但**修了也解决不了上游 500 审核**。

`gpt-5.6-luna`(第 118 行)、`gpt-5.6-terra`(第 127 行) 实读确认**本来就对**（`["image_in"]`，无 thinking/effort），无需改。

验证：`python -c "import tomllib; tomllib.load(...)"` OK；`kimi -m zg-newapi/gpt-5.6-sol` 冒烟返回 OK。

## 3. 渠道 3「百倍」`baibei-100xlabs` 健康核查

| 项 | 值 |
|---|---|
| id / name / type | 3 / `baibei-100xlabs` / 14（Anthropic 原生） |
| status / prio / weight | 1(启用) / 40 / 10，多 key（1~5 轮） |
| base_url | `https://sub.100xlabs.space` |
| models | `claude-opus-4-7` / `claude-opus-4-8` / `claude-opus-5` |
| balance / used_quota | 0.0 / 2060116 |

**当前健康度偏红**：近两次渠道测试 `19:49` → **429 `Concurrency limit exceeded for account`**、`20:21` → **502**；上次测试响应 ~19s。它挂在 claude-opus 故障转移链（日志 `use_channel:["18","3","27"]`，18→3→27），但那次成功由 **27(gorouter)** 兜底，不能证明 3 自身出活。优先级 40 低于其他 claude 渠道（linxi 55/60、gorouter 45），平时仅作靠后兜底。

判读：渠道配置正常、已启用、在池中，但上游 100xlabs 当前**间歇性 429/502**（并发额度受限 + 网关偶发 502），属上游侧波动，非本机配置问题。

## 处置建议

- 上游 500 审核 / 429 并发：本机改不动 provider 策略。碰到时切 `gpt-5.6-luna`/`terra`/`gpt-5.5`（sol 失败时）或依赖 claude 链其它渠道（百倍失败时）。
- config 误标已修复并验证。

## Related

- 上游最小状态：`docs/ops/newapi-vps-minimal-state-2026-07-28.md`
- gorouter claude 链：`docs/patches/gorouter-claude-newapi.md`
