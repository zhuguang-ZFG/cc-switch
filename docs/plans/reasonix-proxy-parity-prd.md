# Reasonix 代理接管与故障路由对齐计划书（PRD）

状态：Design approved（方案 B + 二次源码复核修正）  
目标版本：下一个补丁版本（版本号在发布阶段决定）  
基线分支：`feat/reasonix-app`  
对照基线：Claude Code / Codex / Grok Build / Kimi Code 已落地的代理闭环  
Reasonix 源码基线：`.vendor/DeepSeek-Reasonix`（`esengine/DeepSeek-Reasonix@main-v2`）

## 1. 问题定义

`feat/reasonix-app` 已具备 Reasonix 应用切换、累加供应商写入、`[[plugins]]` MCP、Skills 同步与表单 UI，但**没有**与 Claude / Codex / Kimi 等价的本地代理接管与故障路由闭环。

用户可以在 Reasonix 页管理供应商并写 live `config.toml`，请求仍直连上游：无法使用独立故障转移队列、熔断、热切换、代理用量统计与安全恢复。前端 `ProxyTakeoverStatus.reasonix` 已有类型占位，后端未实现。

本任务不是“再加一个供应商表单”，而是让 Reasonix 从接管开启到请求统计形成可验证、可回滚、可发布的产品能力。

## 2. 已确认事实

### 2.1 已具备能力

- `AppType::Reasonix`、AppSwitcher、可见性、目录覆盖、`reasonixConfigDir`。
- `reasonix_config.rs`：`config.toml` + `.env`、`[[providers]]` 累加 / 切换、`[[plugins]]` MCP、api_key 拆分到 `.env`。
- 前端 `ReasonixFormFields` / presets；DB v16 `enabled_reasonix`（skills/MCP）。
- 托盘 Reasonix 分区；MCP 启动导入 Reasonix plugins。

### 2.2 阻断性缺口

- `proxy_config` CHECK 不含 `reasonix`，无种子行。
- `ProxyTakeoverStatus` 后端未返回 `reasonix`；`set_takeover_for_app` / 恢复 / 崩溃清理未覆盖。
- `ProxyService` 无 Reasonix 备份 / 接管 / 恢复 / 热切换。
- `ProxyServer` 无 `/reasonix/v1/...` 路由。
- forwarder 无 Reasonix 占位模型 remap（`cc-switch-proxy-default`）。
- 设置页故障转移 UI / 用量筛选未纳入 `reasonix`。

### 2.3 源码约束（不可违反）

来自 `.vendor/DeepSeek-Reasonix`：

1. `kind=openai` → `base_url + "/chat/completions"`（可用 `chat_url` 覆盖）。
2. `kind=anthropic` → `{root}/v1/messages`（可 strip 尾部 `/v1`）。
3. Provider 是 `[[providers]]` 数组；密钥只经 `api_key_env` + 全局 `.env`。
4. `default_model` 可为 provider 名或 `provider/model`。
5. 环回地址允许缺 key，但仍应写入 `PROXY_MANAGED` 便于检测。
6. Reasonix 现有 `ccswitch.go` **仅**从 cc-switch 导入 **Codex 启用** MCP，与代理接管无关。

## 3. 产品目标

### G1. 路由能力对齐

Reasonix 可独立开启/关闭本地路由接管；独立供应商队列、熔断、重试、超时；不与 Claude/Codex/Kimi 串流量或串健康状态。

### G2. 供应商生命周期对齐

创建 / 编辑 / 删除 / 切换在未接管与接管状态下遵循同一所有权：不覆盖代理占位、不丢用户其它 `[[providers]]` / `[[plugins]]` / agent 表。

### G3. 协议边界对齐

Live 侧对 Reasonix **固定** OpenAI Chat 入站；上游 openai / anthropic 由代理按 attempt 转换。不伪造私有思维链。

### G4. 使用统计对齐

代理请求按 `app_type=reasonix` 记账，可在总览 / 趋势 / 供应商 / 模型 / 明细过滤。

### G5. 安全与升级闭环

异常退出、端口变化、半接管、备份缺失时，不留下指向已停代理的 Reasonix 配置；关闭接管可恢复。

## 4. 功能需求

### R1. 数据库与迁移（P0）

- `proxy_config` CHECK 加入 `reasonix`；fork marker `fork_migration_reasonix_proxy_v1`。
- 默认超时 / 重试与 Codex / Kimi 首版一致，后续可独立调优。
- 全局 listen 地址 / 端口 / 日志开关镜像覆盖 reasonix 行。
- 新库与旧库各恰好一条 reasonix 配置行。

### R2. Live 接管与恢复（P0）

- 接管前备份完整 `config.toml`（及接管写入的 `.env` key 旧值，若存在）。
- 写入固定 `cc-switch-proxy` 占位（见设计文档 §4）；保留用户其余 providers/plugins。
- 关闭：备份恢复 → SSOT 重建 → 仅清理 CC Switch 占位。
- 幂等；端口变化可识别并重建。

### R3. 协议路由（P0）

- 独立 namespace `/reasonix/v1/chat/completions`、`/reasonix/v1/models`。
- 按 `reasonix` 队列选 provider；独立熔断。
- 占位模型 `cc-switch-proxy-default` fail-closed remap 到 attempt 真实 model。
- attempt `kind=openai` → Chat 上游；`kind=anthropic` → Messages 上游（复用现有转换器）。

### R4. 热切换（P0）

- 接管期间切换供应商：更新 DB current + 备份内 default_model / 投影；Live 仍指向 `cc-switch-proxy`。
- 与 per-app switch lock、failover 热切换共用规则（对齐 Kimi）。

### R5. 前端（P0）

- 接管开关与状态含 Reasonix。
- 故障转移设置页含 Reasonix 队列编辑。
- 用量筛选含 Reasonix。

### R6. 产品边界（已闭环，非缺口）

| 项 | 结论 | 说明 |
|----|------|------|
| Reasonix OAuth / 托管登录 | **N/A** | 官方无对等能力；本产品不虚构 OAuth |
| Responses 入站 | **不适用** | openai kind 走 Chat Completions，非 Responses |
| OpenCode / OpenClaw 代理 | **范围外** | 另开任务；不纳入 Reasonix 代理对齐验收 |
| 上游 `ccswitch.go` | **vendor 补丁已备** | 见 `docs/patches/reasonix-ccswitch-enabled-reasonix.md`；合入上游另跟 |

## 5. 验收标准

1. 开启 Reasonix 接管后，`config.toml` 含 `cc-switch-proxy`，`.env` 含 `CC_SWITCH_PROXY_API_KEY=PROXY_MANAGED`，`default_model` 指向该 provider；用户原有 providers 仍在。
2. Reasonix CLI 请求到达 `POST /reasonix/v1/chat/completions`，用量记 `reasonix`。
3. 配置 P1（openai）+ P2（anthropic）故障队列：P1 失败后成功落到 P2，且上游收到的是真实 model id，不是占位符。
4. 接管中切换供应商：Live 仍为代理；关闭接管后恢复接管前 toml（或 SSOT）。
5. `cargo test` 覆盖：takeover 幂等、占位 remap fail-closed、proxy_config 迁移双跑、热切换不改 Live 代理 URL。
6. `pnpm typecheck` 通过；设置页可见 Reasonix 接管与故障转移。

## 6. 风险

| 风险 | 缓解 |
|------|------|
| `proxy_mode=custom` 时 provider `no_proxy` 被忽略，127.0.0.1 可能被系统代理拐走 | 文档说明；接管检测失败时提示检查 `network.no_proxy` |
| 用户模型名恰好叫 `default` | 占位使用 `cc-switch-proxy-default`，不用裸 `default` |
| toml_edit 破坏注释 / 数组顺序 | 用 DocumentMut；只 upsert 目标 provider + 改 `default_model` |
| 与 Kimi Responses 路径混淆 | Reasonix 独立 Chat handler / namespace；禁止复用 `/kimicode` |
| fork 迁移与上游 schema 冲突 | 独立 marker，不抬 `SCHEMA_VERSION` |

## 7. 发布与回滚

- UI 可隐藏 Reasonix 接管，但恢复路径不可随开关删除。
- 迁移只放宽 CHECK + 插行；旧版忽略未知 app_type 行即可。
- 失败时：关闭接管 → 恢复 Live → 再考虑降级。
