# Kimi Code 功能对齐技术设计

状态：Scope confirmed，已完成 Kimi Code 0.26.0 官方源码审计，等待最终设计评审后定稿

## 1. 设计原则

1. 应用归因与 wire protocol 分离：`app_type=kimicode` 始终不变，协议由当前 attempt 的 provider 类型决定。
2. Live 配置只连接本地代理：真实上游地址和密钥只存在数据库 provider/受控 OAuth 存储中。
3. 完整配置备份优先：Kimi 是 additive TOML，不可只备份当前 provider 片段。
4. 每次 failover attempt 独立适配：P1/P2 可以是不同协议。
5. 显式能力优先于名称猜测：导入时生成标准投影，运行期不依赖字符串品牌推断。
6. 失败原子性：数据库 current、恢复备份、Live 代理占位三份状态必须一起提交或一起回滚。

## 2. 目标数据流

```text
Kimi config.toml
  -> importer (lossless native snapshot)
  -> providers(app_type=kimicode)
       native settings + normalized routing projection
  -> proxy_config(kimicode) / failover queue
  -> /kimicode/v1/responses (stable local ingress)
  -> provider router selects attempt
  -> protocol adapter selected from provider.type
  -> upstream API
  -> response adapter back to Responses
  -> usage logger(app_type=kimicode)
  -> detail + rollup + dashboard
```

稳定入站协议确定使用 OpenAI Responses。Kimi Live 接管写入一个 CC Switch 自有 `openai_responses` provider，Kimi Code 0.26.0 始终以 Responses 请求本地代理。Responses 只是不随热切换变化的本地边界；代理内部每个 attempt 必须读取目标 provider/model 的正式 `type`/`protocol`，重新转换为 Chat、Responses、Anthropic 或 Google/Vertex 上游协议。

## 3. 数据模型

### 3.1 原生配置

数据库 `Provider.settings_config` 继续保存 Kimi 原生字段：

```json
{
  "type": "openai",
  "base_url": "https://example/v1",
  "api_key": "...",
  "models": [{ "id": "model-id", "alias": "provider/model-id" }]
}
```

未知字段必须 round-trip；OAuth 供应商只保存引用，不复制 refresh token。

### 3.2 路由投影

在 `ProviderMeta` 增加或复用标准字段：

- `wireProtocol`: responses/chat/anthropic/google-genai/vertexai
- `authStrategy`: bearer/anthropic/google/oauth-kimi/vertex
- `upstreamModel`: alias 对应的真实 model ID
- `reasoning`: 统一推理能力对象
- `projectionVersion`: 便于重新导入或升级规则

投影可以从原生配置重建，因此不是第二份真值。

### 3.3 数据库迁移

- 新库的 `proxy_config` CHECK 加入 `kimicode`。
- 已有库用 `fork_migration_kimicode_proxy_v1` 重建 `proxy_config`，逐列复制旧数据，再 seed Kimi 行。
- 不增加上游 `SCHEMA_VERSION`，避免未来合并冲突。
- 全局代理配置 UPDATE 继续无 WHERE 镜像所有应用行。

## 4. Live 接管格式

保留两个固定 ID：

- provider: `cc-switch-proxy`
- model alias: `cc-switch-proxy/default`

接管写入：

```toml
default_model = "cc-switch-proxy/default"

[providers.cc-switch-proxy]
type = "openai_responses"
base_url = "http://127.0.0.1:<port>/kimicode/v1"
api_key = "PROXY_MANAGED"

[models."cc-switch-proxy/default"]
provider = "cc-switch-proxy"
model = "cc-switch-proxy-default"
```

原 provider/model/thinking/hooks/permission/mcp 均保留。备份保存接管前完整 TOML。热切换更新备份中的 `default_model` 和必要 provider 投影，但 Live 仍指向固定代理 provider。

## 5. 协议适配架构

新增 `KimiRoutingAdapter` 只负责选择现有转换器，不复制 handler：

| provider.type | 上游协议 | 复用能力 |
|---|---|---|
| `kimi` | OpenAI Chat + Kimi OAuth | Responses <-> Chat |
| `openai` | OpenAI Chat | Responses <-> Chat |
| `openai_responses` | Responses | 透传 + 模型映射 |
| `anthropic` | Anthropic Messages | Responses <-> Anthropic |
| `google-genai` | Google generateContent | Responses <-> Google（首版新增组合转换） |
| `vertexai` | Vertex generateContent | 同上，独立 URL/auth 构建 |

handler 增加 Kimi 专用薄封装：

- `handle_kimicode_responses`
- `handle_kimicode_responses_compact`（仅上游支持时；否则本地明确错误）
- `handle_kimicode_models`（返回接管时的稳定模型目录）

通用 Responses handler 接受 `AppType`、tag 和 parser config 参数；Kimi 不再落入 Codex namespace。

## 6. OAuth 设计

- 新增 `KimiOAuthManager` 或把现有刷新函数抽成可复用服务。
- 用进程内锁合并并发刷新，避免多个请求同时使用同一 refresh token。
- access token 在到期前 30 秒刷新。
- 刷新成功原子写回官方 credentials 文件；失败不删除旧凭据。
- provider adapter 每个 attempt 动态取 token，不能在导入时复制 access token。
- 认证错误不跨到第三方 provider，首版不提供放宽开关。官方实现对同一 OAuth provider 的 401 只做一次 force-refresh，第二次失败返回 `PROVIDER_AUTH_ERROR`；网络刷新失败也不进入 agent/provider 重试。

## 7. 推理字段设计

统一中间表示：

```ts
type ReasoningCapabilities = {
  supportsThinking: boolean;
  alwaysThinking: boolean;
  supportsEffort: boolean;
  effortValues?: string[];
  thinkingParam?: "thinking" | "enable_thinking" | "reasoning_split" | "none";
  effortParam?: "reasoning.effort" | "reasoning_effort" | "thinking_budget" | "none";
  outputFormat?: "reasoning" | "reasoning_content" | "reasoning_details" | "anthropic_blocks";
};
```

映射顺序：用户显式配置 > 官方模型目录能力 > provider preset > 已知平台默认 > 安全关闭。运行期只做结构转换，不凭品牌名覆盖显式配置。

安全约束：

- 不持久化原始私有 CoT。
- 公开 reasoning 内容仅按协议透传。
- 工具调用签名/ID 与 reasoning block 必须成对保留。
- 日志允许记录 reasoning token 数，不记录敏感正文。

## 8. 导入事务

导入分四步，在任何写入前完成解析和校验：

1. 解析完整 TOML，收集 provider/model/default 引用。
2. 校验 provider 类型、URL、认证引用、模型 alias 唯一性。
3. 构建数据库 provider 和 routing projection，生成变更预览。
4. 单事务写数据库；Live 文件不因“导入”被重写。

冲突规则：

- 托管 `managed:kimi-code` 是保留 ID，只能由登录、模型刷新和登出流程修改；普通导入拒绝覆盖。
- 同 source URL + 同 provider ID：上游拥有的 provider/model 字段更新，用户自建 alias、`overrides` 和未知扩展字段保留。
- 同 source URL 的完整刷新：新增上游新增项，删除上游已移除 provider/model；单 provider scoped refresh 不删除同源兄弟项。
- 同 provider ID + 不同 source：不静默覆盖，生成冲突预览；用户显式选择替换，或用 `source hash + provider ID` 生成确定性新 ID。
- 同 ID + 同原生配置：幂等跳过。
- model alias 指向不存在 provider：报告错误并跳过该 model，不静默绑定其他 provider。
- `hermes` 旧数据先规范为 `kimicode`，再参与冲突判断。

## 9. 状态与恢复

接管真值必须同时满足：

- `proxy_config.kimicode.enabled = true`
- Kimi live 存在 CC Switch provider/model 占位
- base_url 与当前实际监听端口一致
- 原始备份存在

任一不满足时，“开启接管”执行重建；“关闭接管”执行备份 -> SSOT -> 清理占位的三级恢复。

## 10. 使用统计

- handler context 固定 `app_type_str="kimicode"`。
- parser 根据最终上游协议解析 token，归一到统一 `TokenUsage`。
- `input_token_semantics` 由协议枚举决定，不再使用 `app_type` 字符串列表推断。
- rollup 无需 Kimi 特例，确保 join `(provider_id, kimicode)` 正确。
- Dashboard 增加 Kimi icon/filter/i18n；后端查询保持通用 app_type 过滤。
- 请求统计区展示请求数、token、缓存、延迟、provider/model、错误和可验证成本。仅当 provider 有明确价格来源时计算成本，价格缺失时显示 token 而非猜测金额。
- 官方额度区只对 `managed:kimi-code` 展示 `/usages` 返回的 5 小时/周窗口、used/limit、重置时间、Extra Usage 余额和月度上限；它不是请求级 token 统计，也不折算成每 token 成本。

## 11. 主线合并策略

- 把 Kimi 特有逻辑限制在 `kimi_config`、`KimiRoutingAdapter`、薄 handler 和枚举扩展。
- 对通用 handler 的改造先参数化再接 Kimi，避免复制大函数。
- fork 数据库迁移独立 marker，合并上游 schema 时先跑上游迁移再跑 fork migration。
- 每次同步上游后运行静态枚举扫描，检查新增 `AppType` match 是否漏 Kimi。

## 12. 回滚

- 功能开关可以隐藏 Kimi 接管 UI，但恢复代码不能随开关禁用。
- 迁移只新增 Kimi row 和放宽 CHECK，不删除旧数据，可由旧版忽略该行。
- 发布前保留 v3.17.2 安装包；新版本失败时先关闭接管并恢复 Live，再降级程序。

## 13. 上游实现证据

### 13.1 主基线：Kimi Code 0.26.0

审计来源：`https://github.com/MoonshotAI/kimi-code`，tag `@moonshot-ai/kimi-code@0.26.0`，commit `36b05820cba24e09fdff19a059afc08ccea2c35e`。只读副本：`D:\tmp\kimi-code-0.26-reference`。本机 `kimi --version` 输出 `0.26.0`，因此该 tag 是兼容性和回归测试的唯一主基线。

`packages/agent-core-v2/src/app/provider/provider.ts` 与 `packages/kosong/src/providers/index.ts` 明确声明六种正式 provider 类型：`kimi`、`openai`、`openai_responses`、`anthropic`、`google-genai`、`vertexai`。`openai` 和 `google-genai` 不是需要改写成 Python 版内部名称的别名；CC Switch 应保留这些配置值，并以显式枚举选择 wire adapter。

### 13.2 Kimi / OpenAI Chat

### 13.2 Kimi / OpenAI Chat

`packages/kosong/src/providers/kimi.ts` 使用 Chat Completions，流式请求显式发送 `stream_options.include_usage=true`。Kimi thinking 写入 `thinking.type=enabled|disabled`，历史 reasoning 以 `reasoning_content` 回传；工具调用参数可能分多块流式返回。缓存输入 token 从 `prompt_tokens_details.cached_tokens` 或兼容字段扣除。

### 13.3 OpenAI Responses

`packages/kosong/src/providers/openai-responses.ts` 使用 `/responses`，推理请求写入 `reasoning.effort` 并请求 `include=["reasoning.encrypted_content"]`。入站历史会把连续同签名的 `ThinkPart` 合并为一个 reasoning item；流式事件分别是 text delta、function call arguments delta、reasoning summary delta 和 completed usage。

### 13.4 Anthropic

`packages/kosong/src/providers/anthropic.ts` 使用 `/messages`，支持旧模型的 `thinking={type:"enabled", budget_tokens}` 和新模型的 adaptive thinking + `output_config.effort`。没有 signature 的 thinking block 会被丢弃；有 signature 时必须原样保留。工具调用使用 `tool_use` / `tool_result`，流式签名通过 `signature_delta` 到达。

### 13.5 Google GenAI / Vertex

`packages/kosong/src/providers/google-genai.ts` 统一调用 `generateContent` / `generateContentStream`。思考内容使用 `part.thought=true`，工具调用可能带 `thoughtSignature`，需要存入工具调用 extras 后在下一轮送回。usage 使用 `promptTokenCount`、`candidatesTokenCount`、`cachedContentTokenCount`。Vertex 复用同一 adapter，通过 `vertexai=true`、project/location 和对应认证参数构建客户端。

### 13.6 OAuth

`packages/oauth/src/oauth-manager.ts` 的 `OAuthManager`：

- provider 只保存 `OAuthRef(storage,key)`，access/refresh token 在外部凭据存储。
- `ensure_fresh()` 先读持久化 token，再按过期阈值刷新；`force=true` 用于 401 后重试。
- 使用进程内 in-flight coalescing；POSIX 上用 `proper-lockfile` 做跨进程刷新锁。官方 0.26.0 在 Windows 明确禁用该文件锁，因此 CC Switch Windows 端必须由自身服务锁和原子凭据写入补足并发保护，不能声称“复用官方跨进程锁”。
- refresh 失败区分 401/403、可重试网络错误和临时失败；不会误删并发进程刚写入的新 token。
- 所有 Kimi API 请求带 `X-Msh-Platform`、版本、设备名、设备模型、OS、设备 ID 等公共头。

CC Switch 的 OAuth 适配必须复用这些语义；仅凭 `expires_at` 读取并直接发送 access token 不合格。

### 13.7 次要对照：Kimi CLI Python 1.49.0

`https://github.com/MoonshotAI/kimi-cli` 的 `1.49.0`（只读副本 `D:\tmp\kimi-cli-reference`）属于另一产品线。其协议细节可用于发现未来兼容风险，但不能改变 0.26.0 的 provider 名称、配置字段或登录行为基线。

### 13.8 设计修订

- 统一中间消息从“文本 reasoning”升级为 `ThinkPart { text, encrypted/signature, source_protocol }`，并允许工具调用 extras 携带 Google `thought_signature_b64`。
- Kimi provider 类型使用 0.26.0 的六值枚举；仅对历史 CC Switch 数据中的旧别名做显式、可审计的迁移，不把 Python CLI 内部名称写回 Kimi `config.toml`。
- usage parser 增加 Kimi Chat、Responses、Anthropic、Google 四套字段映射，并保留 cache/read 与 reasoning signature 不入日志正文。
- OAuth 实现必须具备进程内刷新合并、Windows 服务锁/原子写入和 401 force-refresh 一次重试；POSIX 可额外使用跨进程文件锁。否则不得把官方登录列为“已支持”。

### 13.9 官方文档与社区交叉验证

- 官方配置文档明确：`/login` 托管账号不出现在普通 `/provider` 管理器中，只能用 `/login` / `/logout` 管理；因此 CC Switch 采用“托管入口只读”，而不是允许普通导入覆盖。见 [`docs/zh/configuration/providers.md`](https://github.com/MoonshotAI/kimi-code/blob/36b05820cba24e09fdff19a059afc08ccea2c35e/docs/zh/configuration/providers.md)。
- 官方 provider/model 管理 PR [#264](https://github.com/MoonshotAI/kimi-code/pull/264) 与源码的 `mergeRefreshedModelAlias` 保持一致：远端拥有字段刷新，用户扩展字段和 overrides 保留；完整 registry 刷新会清理同源已删除项。
- 官方 OAuth 修复 PR [#399](https://github.com/MoonshotAI/kimi-code/pull/399) 明确要求按 OAuth host + API base URL 隔离凭据，并在 managed `/models` 401/403 时 force-refresh；这不能推导出跨第三方 provider 故障转移。
- 0.26.0 的 `packages/oauth/src/managed-usage.ts` 明确只为 `managed:kimi-code` 解析 5 小时/周额度、重置时间、Extra Usage 余额和月度上限；官方社区 issue [#872](https://github.com/MoonshotAI/kimi-code/issues/872) 也将这些数据定义为 quota/limit，而不是 token 价格。CC Switch 据此分离“请求统计”和“官方额度”。
