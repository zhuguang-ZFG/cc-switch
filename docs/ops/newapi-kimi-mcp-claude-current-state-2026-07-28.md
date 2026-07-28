# NewAPI、OpenOneAPI、Kimi CLI 与 Claude Agent 当前状态（2026-07-28）

> 这是 2026-07-28 晚间修复后的运维快照。历史路由、guard、自动调权和 Telegram 告警方案仅供追溯，不应作为现网操作依据。
>
> **2026-07-29 更新**：Grok 路由、全局 option 与渠道数以 [newapi-audit-2026-07-29.md](./newapi-audit-2026-07-29.md) 的 Later changes 为准。本文下文中的「grok 三路 17→19→13」已过时——现网主源是 channel 20 `fengwind-grok`（priority 70）；13/17 仍挂 `grok-4.5` 但现测 429；19 仍 disabled。

## 结论

- 保留 NewAPI 作为统一网关，不迁移到 Sub2API。
- `grok-4.5`（**历史**三路，已 superseded）：OpenOneAPI（channel 17，主/priority 60）→ gpt2api（channel 19，轮询/priority 55）→ ai.168661（channel 13，备/priority 50）。channel 19 于 2026-07-28 接入；**2026-07-29 起主源改为 channel 20 `fengwind-grok` priority 70**，见审计文档。
- 日常 `kimi`（Kimi Code 0.29.1）已按当前 schema 分离 OpenAI-compatible 与 Anthropic provider；旧 Python `kimi-cli` 1.48.0 单独保留。
- Kimi MCP 已从 14 项清理为 12 项；保留项均完成真实握手。
- Claude 完整 Agent 请求的 403 已通过单 Key 主备渠道和能力隔离修复。
- 正常 MCP 配置下真实 Kimi Agent 回归成功，退出码为 0。
- 全程未修改、重装或重编 cc-switch，也未调整其数据库 schema。

## 运维边界

日常 NewAPI、Kimi 和 Claude DX 修复遵守以下规则：

1. 不改 `src-tauri/`，不执行 Cargo/Tauri rebuild，不替换 cc-switch 可执行文件。
2. 不升级或迁移 `~\.cc-switch\cc-switch.db` schema。
3. 优先调整 NewAPI 渠道/abilities、provider 环境以及 Kimi/Claude 客户端配置。
4. 文档和提交中禁止出现完整 API Key、Token、VPS 密码或 Telegram session。

绑定规则见 [do-not-modify-cc-switch.md](./do-not-modify-cc-switch.md)。

## 统一调用链

```text
Kimi Code 0.29.1（命令 kimi）
  ├─ OpenAI-compatible 模型 → https://aliyun.donglicao.com/v1
  └─ Claude 原生模型       → https://aliyun.donglicao.com + SDK /v1/messages
                                  │
                                  ▼
                               NewAPI
                                  │
             ┌────────────────────┼────────────────────┐
             ▼                    ▼                    ▼
        OpenOneAPI Grok      Claude 单 Key主备      其他现有渠道
```

Anthropic provider 的 `base_url` 必须使用域名根路径，不能附加 `/v1`，否则 SDK 会请求 `/v1/v1/messages`。

## NewAPI 路由

### Grok 4.5

| 角色 | Channel | 名称 | Priority | Weight | 状态 |
|---|---:|---|---:|---:|---|
| 主 | 17 | `openoneapi-grok` | 60 | 30 | enabled（慢/间歇 503） |
| 轮询 | 19 | `gpt2api-grok` | 55 | 20 | **disabled（2026-07-29 停用，key 无效）** |
| 备 | 13 | `ai.168661-grok` | 50 | 10 | enabled（上游组 429 限流） |

Channel 17 使用 OpenAI-compatible 上游 `https://openoneapi.com`，对外模型为 `grok-4.5`。已验证：

- `/v1/models` 包含 `grok-4.5`；
- Chat Completions 返回 HTTP 200；
- 返回 `reasoning_content`；
- 标准 OpenAI `tool_calls` 可用；
- NewAPI 日志确认正常请求命中 channel 17。

Channel 19（gpt2api）已于 **2026-07-29 停用**（`POST /api/channel/19/status status=2`）。复盘：该渠道 2026-07-28 建立时因 NewAPI 管理面 `POST /api/channel` 触发 Go nil-pointer panic，改用 sqlite **直接复制 channel 17 行**写入——因此它继承了 channel 17（OpenOneAPI）的 key，而非 gpt2api 自己的 key。文档此前"已独立验证 chat/reasoning/tool_calls 200"是**直连上游**测的，从未真正走通网关。经网关实测两个独立故障：

1. **base_url 双 `/v1`**：原值 `https://gpt2api.dpdns.org/v1`，NewAPI 对 OpenAI 类型渠道自动补 `/v1/chat/completions` → 实际打 `/v1/v1/chat/completions` → 404。2026-07-29 已修正为 `https://gpt2api.dpdns.org`（`PUT /api/channel` 最小 payload `{id, base_url}`，DB 值已确认落库）。
2. **key 无效**：修好路径后仍返回 401 `INVALID_API_KEY`——继承的 channel 17 key 在 gpt2api 上游无效。需要正确的 gpt2api.dpdns.org 密钥才能救活，否则保持 disabled。

要恢复：先在 gpt2api 控制台取有效 key，通过 `PUT /api/channel`（带 `key` 字段）写入，再 `POST /api/channel/test/19` 确认 200，最后 `POST /api/channel/19/status status=1` 启用。

OpenOneAPI 返回的模型和额度字段属于平台内部口径，不能仅凭字段名推断为官方模型版本或美元余额。应持续观察模型一致性、价格和供应稳定性。

### Claude Opus 5

| 角色 | Channel | 名称 | Key 形态 | Priority | Weight | `claude-opus-5` |
|---|---:|---|---|---:|---:|---|
| 主 | 9 | `linxi-k40` | 单 Key | 60 | 20 | enabled |
| 备 | 18 | `linxi-k40-opus5-backup` | 单 Key | 55 | 10 | enabled |
| 隔离 | 3 | `baibei-100xlabs` | 原渠道保持 | 50 | 10 | disabled only for this model |

Channel 3 只禁用了 `claude-opus-5` ability，其他模型能力未修改。

不要把多条 Key 直接作为换行字符串写入请求头。若使用 NewAPI 多 Key 模式，必须确保 `channel_info` 中的多 Key 元数据完整且 SQLite 存储类型符合服务端读取约定；本次 Claude 路由为降低风险，最终采用两个独立单 Key 渠道。

### Claude 403 根因

403 不是入口 NewAPI Token、`anthropic-version` 或 `/v1/messages` 路径错误，而是两个上游问题：

1. 原 channel 9 的某个轮询 Key 无权调用 `/v1/messages`；
2. channel 3 对大型 system prompt、工具定义和 thinking 组合的 Agent 请求触发上游 WAF/内容策略，最小请求仍可成功。

手工缩减多 Key 时还曾因换行 Key 被当成单个 `X-Api-Key` 值而触发 `invalid header field value`。拆分单 Key 主备后，该类错误消失。

## Kimi Code 与旧 Python kimi-cli 的版本边界

本机同时安装了两套不同的 CLI，schema 不兼容：

| 命令 | 实际路径 | 版本 | 默认配置 |
|---|---|---:|---|
| `kimi` | `C:\Users\zhugu\.kimi-code\bin\kimi.exe` | Kimi Code 0.29.1 | `C:\Users\zhugu\.kimi-code\config.toml` |
| `kimi-cli` | `C:\Users\zhugu\.local\bin\kimi-cli.exe` | Python kimi-cli 1.48.0 | `C:\Users\zhugu\.kimi\config.toml` |

现代 Kimi Code 官方使用 `KIMI_CODE_HOME` 覆盖数据目录；旧 Python 1.48 使用 `KIMI_SHARE_DIR`。不要让两者共用同一 `config.toml`，也不要把两者的 provider type 混写。

用户日常执行的是 `kimi`，因此以下现网配置以 Kimi Code 0.29.1 schema 为准。

### Provider 分流

现代 Kimi Code 的 OpenAI-compatible 模型使用：

```toml
[providers.zg-newapi]
type = "openai"
base_url = "https://aliyun.donglicao.com/v1"
api_key = ""

[providers.zg-newapi.oauth]
storage = "file"
key = "oauth/zg-newapi"
```

Claude 原生 Messages 使用：

```toml
[providers.zg-newapi-anthropic]
type = "anthropic"
base_url = "https://aliyun.donglicao.com"
api_key = ""

[providers.zg-newapi-anthropic.oauth]
storage = "file"
key = "oauth/zg-newapi"
```

NewAPI Token 已从 TOML 外移到 Kimi credential storage。凭据文件和旧配置备份均属于敏感文件，不得提交。

现代 Kimi Code 接受 `type = "openai"`，不接受旧 Python 1.48 的 `openai_legacy`；旧 Python 1.48 恰好相反。现代模型 schema 支持 `provider`、`model`、`max_context_size`、`capabilities`、`display_name`，并可选用 `max_output_size`、`reasoning_key`、`adaptive_thinking` 等现代字段。不要把两代 CLI 的字段约束混用。

### Thinking 与上下文

全局设置：

```toml
default_thinking = true
show_thinking_stream = true

[thinking]
mode = "on"
effort = "high"

[loop_control]
max_steps_per_turn = 1000
reserved_context_size = 50000
compaction_trigger_ratio = 0.85
```

已验证：

- `glm-5.2`：OpenAI Chat Completions 返回 `reasoning_content`；
- `grok-4.5`：返回 `reasoning_content`，工具调用成功；
- `claude-opus-5`：Anthropic `/v1/messages` 返回 thinking block 和 tool use。

Kimi 自动压缩在以下任一条件满足时触发：

```text
context_tokens + reserved_context_size >= max_context_size
context_tokens >= max_context_size * compaction_trigger_ratio
```

`max_context_size` 是客户端调度声明。若上游没有公开 context/output 元数据，不能把该值描述为实测硬上限。

## Kimi MCP 当前状态

### 保留并验证可用（12 项）

- `fetch`
- `fz-sim`
- `headroom`
- `code-rag`
- `stackoverflow`
- `context-mode`
- `kimi-code`
- `agent-inspect`
- `kimi-mneme`
- `linux-do`
- `platformio`
- `serial-log`

### 已从 `mcpServers` 移除（2 项）

- `telegram-mcp`：authorization session 已作废；
- `esp-idf-tools`：目标 ESP-IDF v6.0.1 环境未安装或不可用。

本次实测的 MCP 配置仍会把带 `enabled=false` 的条目列入加载清单。要真正禁用失效服务，必须从 `mcpServers` 中移除该条目。此次只修改了配置引用，未删除程序、虚拟环境、wrapper 或凭据。

## 回归结果

| 检查 | 结果 |
|---|---|
| OpenOneAPI `grok-4.5` Chat Completions | HTTP 200 |
| Grok thinking / tool calls | 通过 |
| Claude `/v1/messages` 最小请求 | 通过 |
| Claude system + tools + thinking Agent 请求 | HTTP 200，返回 `tool_use` |
| 空 MCP 隔离下 Kimi Agent | 成功，exit 0 |
| 正常 12 MCP 下 Kimi Agent | `MCP_AND_CLAUDE_OK`，exit 0 |
| Kimi 模型配置实例化 | 21 个模型通过 |

## 备份与回滚点

NewAPI 数据库备份：

- `/opt/new-api/data/backups/one-api.before-kimi-cli-20260728-205008.db`
- `/opt/new-api/data/backups/one-api.before-openoneapi-20260728-211135.db`
- `/opt/new-api/data/backups/one-api.before-claude-key-fix-20260728-212544.db`
- `/opt/new-api/data/backups/one-api.before-claude-split-20260728-213036.db`

本机配置备份：

- `C:\Users\zhugu\.kimi-code\config.toml.bak.newapi-20260728-204934`
- `C:\Users\zhugu\.kimi-code\mcp.json.bak.cleanup-20260728-213243`

本机备份可能包含旧 Token 或失效 session，只用于本机恢复，禁止复制到仓库或公开工单。

## 后续注意事项

1. OpenOneAPI Key 曾进入交互和 NewAPI 数据库；稳定后应在上游控制台轮换，再同步更新 channel 17。
2. 上游仍可能出现瞬时 500、并发限制或网络错误；由 NewAPI 重试和 channel 9/18 主备吸收。
3. 若恢复 Telegram MCP，必须重新生成独立 session；不能复用已作废 session。
4. 若恢复 ESP-IDF MCP，先安装并验证包含 MCP 能力的 ESP-IDF 环境，再重新添加配置。
5. 历史 guard、路由脚本、Telegram 告警和自动调权文档不得直接用于现网；当前 VPS 极简边界见 [newapi-vps-minimal-state-2026-07-28.md](./newapi-vps-minimal-state-2026-07-28.md)。
