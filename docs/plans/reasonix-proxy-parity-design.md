# Reasonix 代理接管与故障路由对齐技术设计

状态：Design approved（方案 B）——等待实现前最终确认  
对照文档：`docs/plans/reasonix-proxy-parity-prd.md`、`docs/plans/kimi-parity-design.md`  
Reasonix 源码：`D:\Users\cc-switch\.vendor\DeepSeek-Reasonix`（`main-v2`）  
cc-switch 对照实现：`kimi_config::apply_proxy_takeover`、`/kimicode/v1/responses`、`services/proxy.rs`、`proxy/providers/codex.rs`（占位 remap）

---

## 1. 设计原则

1. **应用归因与 wire protocol 分离**：`app_type=reasonix` 始终不变；上游协议由当前 attempt 的 `kind`（openai / anthropic）决定。
2. **Live 只连本地代理**：真实 `base_url` / 密钥只在 DB provider（及 `.env` 非代理条目）中；Live 占位不暴露用户密钥。
3. **完整配置备份优先**：Reasonix `config.toml` 是多表（`[[providers]]` / `[[plugins]]` / agents）additive 文档，不可只备份当前 provider 片段。
4. **每次 failover attempt 独立适配**：P1 可为 openai Chat，P2 可为 anthropic Messages。
5. **显式 kind 优先于名称猜测**：运行期读 attempt 的 `settings_config.kind`（或等价投影），不根据供应商显示名猜协议。
6. **失败原子性**：DB `proxy_config.reasonix.enabled`、备份文件、Live 代理占位三份状态一起提交或一起回滚。
7. **源码契约优先**：入站与出站路径必须与 Reasonix Go 客户端实际拼接规则一致，禁止凭“常识”假设 `/v1` 语义。

---

## 2. 目标数据流

```text
Reasonix config.toml + .env
  -> (可选) 导入 / 用户在 UI 创建
  -> providers(app_type=reasonix)
       settings_config: { kind, base_url, models[], default, api_key, ... }
  -> proxy_config(reasonix) / failover queue
  -> Live 接管: [[providers]] cc-switch-proxy -> 127.0.0.1:<port>/reasonix/v1
  -> Reasonix CLI (kind=openai)
       POST {base_url}/chat/completions
       model = cc-switch-proxy-default
  -> ProxyServer /reasonix/v1/chat/completions
  -> reasonix 独立队列选 attempt
  -> apply_reasonix_upstream_model (占位符 -> 真实 model)
  -> protocol adapter by attempt.kind
       openai    -> Chat Completions 上游
       anthropic -> Anthropic Messages 上游（Chat 入站转 Messages）
  -> usage logger(app_type=reasonix)
  -> detail + rollup + dashboard
```

**稳定入站协议：OpenAI Chat Completions。**  
原因：Reasonix `internal/provider/openai/openai.go` 对 `kind=openai` 固定 `base_url + "/chat/completions"`。Live 占位必须用 `kind=openai`，这样 CLI 不感知代理存在。

Responses **不是** Reasonix 一等入站协议；不要复用 `/kimicode/v1/responses`。

---

## 3. 数据模型

### 3.1 原生配置（DB `Provider.settings_config`）

Reasonix 供应商在数据库中保存可往返的原生字段（与现有表单一致）：

```json
{
  "kind": "openai",
  "base_url": "https://api.example.com/v1",
  "api_key": "...",
  "models": ["model-a", "model-b"],
  "default": "model-a",
  "api_key_env": "OPTIONAL_CUSTOM_ENV",
  "no_proxy": false,
  "chat_url": null,
  "models_url": null
}
```

| 字段 | 含义 | 代理侧用法 |
|------|------|------------|
| `kind` | `openai` \| `anthropic` | 选择上游 adapter |
| `base_url` | 上游根 URL | openai：直接拼 `/chat/completions`；anthropic：按 Reasonix 规则取 root |
| `models` | 模型 id 列表 | `/models` 与 remap 候选 |
| `default` | 默认模型 id | 占位符优先映射目标 |
| `api_key` | 明文密钥（DB） | 上游请求；**不**写入 Live toml |
| `api_key_env` | 环境变量名 | 非接管写入 `.env` 时使用 |
| `no_proxy` | 用户侧绕过系统代理 | Live 占位强制 `true` |
| `chat_url` / `models_url` | 可选覆盖 | 代理出站可透传；Live 占位**不设** `models_url` |

未知字段必须 round-trip（导入/导出不丢）。

### 3.2 路由投影（可选，首版可内联）

若后续与 Kimi 统一投影层，可增加：

- `wireProtocol`: `chat` | `anthropic`
- `authStrategy`: `bearer` | `anthropic-x-api-key`
- `upstreamModel`: 真实 model id
- `projectionVersion`: 规则版本号

首版允许在 forwarder 内直接读 `settings_config`，但**禁止**用供应商 name 字符串推断协议。

### 3.3 `proxy_config` 行

| 列 | reasonix 首版默认 | 说明 |
|----|-------------------|------|
| `app_type` | `reasonix` | CHECK 必须包含 |
| `enabled` | `0` | 接管开关 |
| `proxy_base_url` / `proxy_port` | 与全局一致 | 镜像更新 |
| `failover_enabled` | 对齐 Codex/Kimi 默认 | |
| 超时 / 重试 / 熔断列 | 对齐 Codex 首版 | 后续可独立调优 |

### 3.4 数据库迁移

**新库**：在 `schema.rs` 的 `proxy_config` CHECK 与 seed 中加入 `reasonix`。

**已有库**：fork 迁移 `fork_migration_reasonix_proxy_v1`（模式对齐 `fork_migration_kimicode_proxy_v1`）：

1. 若 marker 已存在 → no-op。
2. 重建 `proxy_config` 表：CHECK 含 `reasonix`；逐列复制旧行。
3. `INSERT OR IGNORE` 一条 `reasonix` 默认行。
4. 写 marker。

约束：

- **不**增加上游 `SCHEMA_VERSION` / `user_version`，避免未来合并冲突。
- 全局代理配置 `UPDATE`（listen / port / log）继续**无 WHERE** 镜像所有应用行（含 reasonix）。

---

## 4. Live 接管格式

### 4.1 固定 ID

| 用途 | 值 | 说明 |
|------|-----|------|
| provider `name` | `cc-switch-proxy` | 与 Kimi/Claude 命名空间一致 |
| model 列表项 | `cc-switch-proxy-default` | **禁止**裸名 `default`（与现有 `is_cc_switch_proxy_model` 对齐） |
| `default_model` | `cc-switch-proxy` 或 `cc-switch-proxy/cc-switch-proxy-default` | 见 §4.3 |
| `api_key_env` | `CC_SWITCH_PROXY_API_KEY` | 密钥不进 toml |
| `.env` 值 | `PROXY_MANAGED` | 便于检测；环回虽允许空 key，仍写入 |

### 4.2 写入形态（方案 B）

```toml
default_model = "cc-switch-proxy"

[[providers]]
name        = "cc-switch-proxy"
kind        = "openai"
base_url    = "http://127.0.0.1:<port>/reasonix/v1"
models      = ["cc-switch-proxy-default"]
default     = "cc-switch-proxy-default"
api_key_env = "CC_SWITCH_PROXY_API_KEY"
no_proxy    = true
# 故意不设 models_url / chat_url
```

`.env`（Reasonix 配置目录）：

```env
CC_SWITCH_PROXY_API_KEY=PROXY_MANAGED
```

### 4.3 `default_model` 解析规则（源码）

Reasonix 允许：

- 纯 provider 名 → 使用该 provider 的 `default` / `models[0]`
- `provider/model` → 指定模型

接管推荐：`default_model = "cc-switch-proxy"`（依赖 provider 内 `default`），与写入的 `default = "cc-switch-proxy-default"` 一致。  
备选：`default_model = "cc-switch-proxy/cc-switch-proxy-default"`（更显式，二选一写死并测通即可）。

### 4.4 URL 拼接（关键契约）

Reasonix openai client：

```text
上游请求 URL = base_url + "/chat/completions"
```

因此 Live `base_url` **必须**是：

```text
http://127.0.0.1:<port>/reasonix/v1
```

最终 CLI 请求：

```text
POST http://127.0.0.1:<port>/reasonix/v1/chat/completions
```

代理路由注册：

```text
POST /reasonix/v1/chat/completions
GET  /reasonix/v1/models
```

**错误示例（禁止）：**

- `base_url = "http://127.0.0.1:<port>/reasonix"` → 会打到 `/reasonix/chat/completions`（缺 `/v1`）
- `base_url = "http://127.0.0.1:<port>/v1"` → 归因到通用/Codex 路径

### 4.5 `no_proxy` 与系统代理

- Live 占位 **必须** `no_proxy = true`。
- Reasonix 在 `proxy_mode=auto|env` 时会把该 host 加入 DirectHosts。
- 若用户全局 `proxy_mode=custom`，**provider 级 `no_proxy` 无效**（源码行为）。产品侧：接管失败或连不上本地代理时，提示检查 Reasonix `network.proxy_mode` / `network.no_proxy`。

### 4.6 备份内容

接管前备份：

1. 完整 `config.toml` 字节（优先）或等价可恢复快照。
2. 若 `.env` 中已存在 `CC_SWITCH_PROXY_API_KEY`，备份其旧值；否则记录“键不存在”，以便关闭时删除或还原。

备份路径：与现有 Claude/Codex/Kimi 代理备份目录约定一致（`ProxyService` 现有 layout），文件名区分 `reasonix`。

### 4.7 接管检测真值

同时满足才视为“已正确接管”：

1. `proxy_config.reasonix.enabled = true`
2. Live 存在 `name = "cc-switch-proxy"` 且 `kind = "openai"`
3. 该 provider `base_url` 指向当前实际监听的 `http://127.0.0.1:<port>/reasonix/v1`（或配置的 listen host）
4. `.env` 含 `CC_SWITCH_PROXY_API_KEY=PROXY_MANAGED`（推荐；环回缺 key 仍可能工作，但不算完整接管）
5. 原始备份存在

任一不满足：

- “开启接管” → 重建占位 + 必要时重写备份策略（幂等 upsert）
- “关闭接管” → 备份恢复 → SSOT → 清理占位

### 4.8 与累加写入的关系

现有 `reasonix_config` 累加 / 切换供应商逻辑在**未接管**时继续写真实 upstream。  
**已接管**时：

- UI 切换供应商 → **不得**把 Live `default_model` 改成真实上游 provider；应走热切换（§7）。
- 创建/编辑真实供应商 → 写 DB；可选同步到**备份** toml 的对应 `[[providers]]`，或仅 DB 为 SSOT、关闭接管时从 DB 重建（与 Kimi 策略对齐，实现时二选一并写清）。

推荐（对齐 Kimi）：接管期间 Live 只保证代理占位；用户供应商变更写 DB + 更新备份中的非占位部分。

---

## 5. 协议适配架构

### 5.1 入站

| 路径 | Handler | `app_type` |
|------|---------|------------|
| `POST /reasonix/v1/chat/completions` | `handle_reasonix_chat`（可薄封装现有 Chat handler） | `reasonix` |
| `GET /reasonix/v1/models` | `handle_reasonix_models` | `reasonix` |

入站请求体为标准 Chat Completions JSON；`model` 通常为 `cc-switch-proxy-default`。

### 5.2 占位模型映射

新增 `apply_reasonix_upstream_model`（对齐 `apply_kimi_upstream_model` / Codex 占位逻辑）：

1. 若 `model` **不是** CC Switch 占位符 → 原样保留（高级用户直连指定模型）。
2. 若是占位符（`cc-switch-proxy-default` 或统一 `is_cc_switch_proxy_model` 判定）：
   - 取 attempt provider 的 `default`，否则 `models[0]`
   - 二者皆空 → **fail-closed** 返回明确错误（禁止静默落到其它供应商模型）
3. 映射后的 id 用于上游请求与用量“真实模型”字段；占位名可记入“请求模型”审计字段。

### 5.3 出站适配

| attempt `kind` | 上游 | 实现策略 |
|----------------|------|----------|
| `openai` | `POST {base_url}/chat/completions` | 透传/轻量改写 model + Authorization |
| `anthropic` | `POST {root}/v1/messages` | 复用现有 Chat→Anthropic Messages 转换（Codex/Claude 路径已有） |

认证：

- openai：`Authorization: Bearer <api_key>`
- anthropic：`x-api-key` + `anthropic-version`（跟现有 anthropic forwarder）

### 5.4 流式

Reasonix openai 客户端支持 SSE streaming。代理必须：

- 对 openai 上游透传 stream
- 对 anthropic 上游做流式转换回 OpenAI Chat SSE（复用现有转换器）
- usage 在 stream 结束时解析并记账

### 5.5 产品边界（已闭环，非缺口）

| 项 | 结论 |
|----|------|
| 入站 Anthropic | 不实现：Live 占位固定 `kind=openai`（出站 anthropic 仍支持） |
| 入站 Responses | **不适用**：CLI 只走 Chat Completions |
| Google / Vertex | 不实现：官方 kind 仅 openai/anthropic |
| Reasonix OAuth | **N/A**：上游无对等托管登录 |
| OpenCode / OpenClaw 代理 | **范围外**：另开任务，不纳入本设计验收 |
---

## 6. ProxyService 集成点

对齐 Kimi 的方法面，Reasonix 需要：

| 能力 | 行为 |
|------|------|
| `backup_reasonix_config` | 备份 toml + 相关 env key |
| `apply_reasonix_takeover` | upsert 占位 provider、设 default_model、写 env、必要时启代理进程 |
| `restore_reasonix_from_backup` | 恢复 toml/env |
| `restore_reasonix_from_ssot` | 备份缺失时从 DB providers 重建 live |
| `cleanup_reasonix_proxy_provider` | 仅删除 `cc-switch-proxy` 与对应 env key |
| `sync_reasonix_live_on_switch` / 热切换 | 见 §7 |
| 崩溃恢复 / 退出恢复 | 与其它 app 一并恢复或清理半接管 |

`set_takeover_for_app(AppType::Reasonix, enabled)` 必须进入与 Claude/Codex/Kimi 相同的启停编排：

- enable：确保 proxy 监听 → 备份 → 写占位 → DB enabled=1
- disable：DB enabled=0 → 恢复备份/SSOT → 若无其它 app 需要则停监听（现有共用逻辑）

`ProviderService::switch` 对 Reasonix：

- **移除**任何“提前 return 绕过代理”的分支（若存在）
- 接管中走通用 switch lock + 热切换
- 未接管走现有累加/切换 live 逻辑

---

## 7. 热切换

### 7.1 语义

接管期间用户切换 current provider：

1. DB：`providers` 的 current / 排序 / failover 队首更新（与现有一致）。
2. **备份** toml：更新 `default_model` 指向新真实 provider（或更新投影），保证关闭接管后正确。
3. **Live** toml：`default_model` 与 `cc-switch-proxy` 块**不变**（仍指向本地代理）。
4. 内存中的 failover 队列 / 熔断状态按现有 Kimi/Codex 热切换规则刷新。

### 7.2 故障转移队列

- 队列成员均为 `app_type=reasonix` 的 provider id。
- 健康检查 / 熔断 key 带 `reasonix` 前缀或独立 map，禁止与 `kimicode`/`codex` 共用。
- 单次请求：按队列顺序 attempt；可重试错误进下一 provider；认证错误策略对齐现有（通常不跨 provider 用错误密钥重试——跟 Codex/Kimi 现行策略）。

---

## 8. 导入与 SSOT（本轮范围）

### 8.1 本轮最小集

代理闭环**不强制**完整 live→DB 导入，但关闭接管的 SSOT 路径需要：

- DB 中已有 reasonix providers 时可重建 live（非占位）
- 或备份优先

若尚未实现 `get_reasonix_live_provider_ids` / live 导入：SSOT 重建以 DB 为准；文档标明“首次使用请先在 UI 创建供应商再接管”。

### 8.2 后续可选项（非本轮验收）

- 从 live `[[providers]]` 导入到 DB（跳过 `cc-switch-proxy`）
- 冲突规则对齐累加语义（同 name upsert）

---

## 9. 使用统计

- handler context 固定 `app_type_str = "reasonix"`。
- token 解析：openai Chat usage / anthropic usage 各走现有 parser。
- Dashboard / 筛选器增加 Reasonix（icon 复用 AppSwitcher 资源）。
- 请求明细区分：客户端声明的 model（占位）vs 上游真实 model。

---

## 10. 前端

| 区域 | 改动 |
|------|------|
| 代理接管状态 | `ProxyTakeoverStatus.reasonix: boolean` 前后端打通 |
| 设置 · 故障转移 | 应用列表增加 Reasonix；队列编辑绑定 `reasonix` |
| 用量 | app filter 增加 `reasonix` |
| i18n | zh / zh-TW / en / ja 补文案 |

UI 文案避免承诺“官方 OAuth”或“Responses”。

---

## 11. 主线合并策略

- Reasonix 特有逻辑限制在：`reasonix_config`、薄 handler、`apply_reasonix_upstream_model`、枚举扩展、fork migration。
- 通用 Chat / Anthropic 转换器先参数化 `AppType` 再接入，避免复制大函数。
- 每次同步上游后静态扫描 `AppType` match，防止漏 `Reasonix`。

---

## 12. 回滚

- 功能开关可隐藏 Reasonix 接管 UI；恢复代码不可删除。
- 迁移只放宽 CHECK + 插行。
- 发布失败：关闭接管 → 确认 Live 恢复 → 再降级安装包。

---

## 13. 上游实现证据（Reasonix）

### 13.1 仓库与基线

- 上游：`https://github.com/esengine/DeepSeek-Reasonix`
- 本地只读副本：`D:\Users\cc-switch\.vendor\DeepSeek-Reasonix`（分支 `main-v2`）
- 配置路径（Windows）：`%APPDATA%\reasonix\config.toml` + `.env`

### 13.2 OpenAI kind

`internal/provider/openai/openai.go`：

- 默认 Chat URL = `strings.TrimRight(base_url, "/") + "/chat/completions"`
- 可用 `chat_url` 完全覆盖
- `models_url` 用于 `/models` 探测；代理占位不设，避免无意义探测

### 13.3 Anthropic kind

`internal/provider/anthropic/`：

- Messages 路径基于 root；若 `base_url` 以 `/v1` 结尾会 strip 再拼 `/v1/messages`
- 代理出站应对齐同一规则，避免双 `/v1`

### 13.4 配置加载

- `[[providers]]` 数组；`api_key_env` 从进程环境 / `.env` 解析
- `default_model` 解析 provider 或 `provider/model`
- `no_proxy` 与全局 `network.proxy_mode` 交互见 §4.5

### 13.5 cc-switch 集成（上游）

`internal/config/ccswitch.go`：

- 仅查询 cc-switch DB 中 **Codex 启用** 的 MCP，写入 Reasonix plugins
- **无**代理接管、**无** failover
- 本设计不修改该上游文件；MCP 启用位对齐属独立任务

### 13.6 与 Kimi 设计的差异摘要

| 项 | Kimi | Reasonix |
|----|------|----------|
| Live provider 类型 | `openai_responses` | `openai`（toml `kind`） |
| 入站 | `/kimicode/v1/responses` | `/reasonix/v1/chat/completions` |
| 配置形态 | `[providers.id]` 表 | `[[providers]]` 数组 |
| 密钥 | 可写在 toml `api_key` | **仅** `api_key_env` + `.env` |
| 占位模型 | `cc-switch-proxy/default` 等 | `cc-switch-proxy-default` |
| OAuth | 官方托管 | 无 |
| 上游 kind 数 | 6 | 2（openai / anthropic） |

---

## 14. 测试计划

### 14.1 单元测试

1. `apply_proxy_takeover` 幂等：两次调用仍仅一个 `cc-switch-proxy`；`default_model` 正确；`.env` 含 `PROXY_MANAGED`。
2. `base_url` 端口替换：监听端口变更后重建，旧端口不再出现。
3. `apply_reasonix_upstream_model`：占位 → default；占位且无 model → 错误；非占位透传。
4. fork migration：空库 / 旧库 / 双跑 marker。
5. 热切换：Live `base_url` 仍为 `/reasonix/v1`；备份中 default 更新。

### 14.2 集成 / 手动

1. 真实 Reasonix CLI：接管后跑一条 chat，确认命中本地端口。
2. P1 故意错误密钥 + P2 正确：failover 成功。
3. P1 openai + P2 anthropic：协议转换成功。
4. 关闭接管：toml 回到备份；CLI 直连上游。
5. 杀进程半接管：下次启动恢复或清理，不留死地址。

### 14.3 回归

- `cargo test`（reasonix_config + proxy 相关）
- `cargo check --lib`
- `pnpm typecheck`
- 现有 Kimi/Claude/Codex 接管用例不破坏

---

## 15. 实现任务拆分（供 implement 文档引用）

见 `docs/plans/reasonix-proxy-parity-implement.md`。

---

## 16. 设计修订记录

| 日期 | 修订 |
|------|------|
| 2026-07-20 | 初版：方案 B；源码复核禁止裸 `default`；入站固定 Chat；`no_proxy`/`proxy_mode=custom` 风险写入；明确不改上游 `ccswitch.go` |
