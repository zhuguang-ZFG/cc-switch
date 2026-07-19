# Kimi Code 功能对齐计划书（PRD）

状态：Scope confirmed，主兼容基线已固定，等待最终设计评审后进入实现  
目标版本：下一个补丁版本（版本号在发布阶段决定）  
基线：`main@a9962c8b93c0b421906ba36f8d879606ec5b4489`

## 1. 问题定义

当前版本已经能展示、登录、编辑和切换 Kimi Code 供应商，但没有完成与 Claude Code / Codex 等价的路由闭环。用户在 Kimi Code 页面能看到“路由/故障转移”相关入口或全局代理状态，却无法获得完整的数据库配置、Live 接管、协议转发、热切换、使用统计和安全恢复能力。

本任务不是“增加一个 Kimi 标签页”，而是让 Kimi Code 从供应商导入到请求统计的整条链路成为可验证、可回滚、可发布的产品能力。

## 2. 已确认事实

### 2.1 已具备能力

- `AppType::KimiCode`、应用切换、显示设置、目录覆盖已经存在。
- `src-tauri/src/kimi_config.rs` 已支持结构化读取/写入 `config.toml`，并保留 `thinking`、`hooks`、`permission` 等无关表。
- Kimi 官方 OAuth 登录、凭据持久化、模型拉取和托管供应商 `managed:kimi-code` 已存在。
- Kimi 自定义供应商支持 `kimi`、`anthropic`、`openai`、`openai_responses`、`google-genai`、`vertexai` 六种原生类型。
- Codex 代理已有 Responses 到 OpenAI Chat / Anthropic 的双向转换，以及 Kimi/DeepSeek 等 `reasoning_content` 处理基础。

### 2.2 阻断性缺口

- `proxy_config` 的 SQLite CHECK 约束不允许 `kimicode`，没有 Kimi 路由配置行。
- 后端 `ProxyTakeoverStatus`、停止保护、崩溃恢复和接管状态检查均未包含 Kimi。
- `ProxyService` 仅实现 Claude、Codex、Grok Build 的备份、接管、恢复、Live 同步和热切换。
- `ProxyServer::build_router()` 没有 Kimi 独立路由；现有无前缀路由会归因到 Codex。
- `ProviderService::switch()` 对 Kimi 有提前返回分支，会绕过通用接管锁与代理热切换。
- 设置页故障转移只显示 Claude、Codex、Grok Build。
- 使用统计的前端应用类型和筛选器不包含 `kimicode`。
- Kimi 导入结果没有形成代理所需的统一协议元数据、模型映射和推理能力契约。
- Kimi 官方 OAuth 在代理内没有刷新/注入策略；直接读取过期 access token 不满足长期运行要求。

## 3. 产品目标

### G1. 路由能力对齐

Kimi Code 可独立开启/关闭本地路由接管，使用独立的供应商队列、熔断器、重试和超时配置，不与 Codex/Grok Build 串流量或串健康状态。

### G2. 供应商生命周期对齐

导入、创建、编辑、删除、切换、官方登录生成的 Kimi 供应商，在未接管与接管状态下都遵循同一套所有权规则，不覆盖代理占位配置，不丢失原始 TOML 内容。

### G3. 思维链语义对齐

系统只处理 API 提供的推理字段和推理配置，不伪造或展示模型私有思维链。请求侧正确映射 thinking/effort/budget，响应侧保留公开的 reasoning 摘要或结构化字段，工具调用前后的 reasoning 连续性不丢失。

### G4. 使用统计对齐

Kimi 代理请求按 `app_type=kimicode` 独立记账，可在总览、趋势、供应商、模型和请求明细中过滤；模型映射前名称、真实上游模型、计价模型分别可审计。

### G5. 安全与升级闭环

升级、异常退出、端口变化、半接管、备份缺失、官方 token 过期、供应商切换失败时，不留下指向已停止代理的 Kimi 配置，不丢失官方登录与自定义配置。

## 4. 功能需求

### R1. 数据库与迁移（P0）

- `proxy_config` 支持 `kimicode` 行，默认值与 Codex 先保持一致，后续可独立调优。
- 使用 fork 自有迁移标记，不占用上游 SQLite `user_version` 命名空间。
- 迁移必须保留现有所有代理配置列和值，并对已执行 Kimi v1 数据迁移的数据库继续生效。
- 全局监听地址、端口、日志开关镜像更新覆盖 Kimi 行。
- 新库和旧库都必须得到且只得到一条 Kimi 配置行。

### R2. Live 配置接管与恢复（P0）

- 接管前原样备份完整 Kimi `config.toml`，包括未知字段、注释和顺序可接受范围内的结构。
- 接管时写入保留命名空间的本地代理 provider/model，不修改或删除用户原 provider。
- Kimi 请求必须访问带应用前缀的地址，避免归因到 Codex。
- 关闭接管优先恢复备份；备份缺失时从数据库 SSOT 重建；再失败时只清理 CC Switch 自有占位项。
- 重复开启/关闭幂等；端口变化后能识别旧地址并重建。
- 异常退出恢复、应用正常退出恢复、手动停止恢复均覆盖 Kimi。

### R3. 协议路由与供应商适配（P0）

- Kimi 使用独立 `/kimicode/...` namespace。
- 每次请求按 `kimicode` 队列选择 provider，并写入 Kimi 独立熔断状态。
- Kimi provider 的 `type` 映射为明确的上游 wire protocol，不通过供应商名称猜测协议。
- `openai` / `kimi`：OpenAI Chat 路径。
- `openai_responses`：Responses 路径。
- `anthropic`：Anthropic Messages 路径。
- `google-genai` / `vertexai`：Google 原生路径，首版必须支持。
- 混合协议故障转移时，每个 attempt 都依据目标 provider 重新转换请求与响应，不能以 P1 的协议假定 P2。
- 模型必须从 Kimi `models[]`/alias 明确映射到真实上游 ID。

### R4. 官方 OAuth（P0）

- `managed:kimi-code` 可作为路由目标和故障转移成员。
- 代理按需取得有效 access token；过期前刷新并原子写回，不把 refresh token 写入请求日志或普通 provider JSON。
- 401 只允许一次受控强制刷新重试；仍失败则返回认证错误并要求重新登录，不自动切换到第三方 provider。403 按上游明确错误处理，不伪装成可恢复的 provider 故障。
- 登出或凭据缺失时，接管开启必须给出可操作错误，不写半成品 Live 配置。

### R5. 供应商导入与编辑（P0）

- 导入 Kimi Live provider 时保留原生 `type/base_url/api_key/oauth/models` 和未知扩展字段。
- `managed:*` 供应商不进入普通供应商管理入口，只能通过官方登录/登出管理。provider 身份、OAuth 引用、端点和官方模型字段只读；用户自建 alias、`overrides`、默认模型选择及非官方扩展字段允许保留。
- 模型 alias、provider 引用和默认模型必须做引用完整性校验。
- 重复导入幂等；同 source + 同 ID 更新上游拥有字段并保留用户字段，同 source 已删除条目同步删除；同 ID + 不同 source 不静默覆盖，要求显式替换或使用确定性新 ID。
- 接管期间新增/编辑/删除供应商只更新数据库和“恢复备份”，不能破坏 Live 代理占位项。
- 导入后为代理生成协议类型、认证策略、模型映射和推理能力投影；原始 Kimi 配置仍是可逆真值。

### R6. 思维/推理设计（P0）

- 定义统一能力模型：`supportsThinking`、`alwaysThinking`、`supportsEffort`、`effortValues`、`thinkingParam`、`effortParam`、`outputFormat`。
- 官方模型能力优先来自官方 models API；自定义模型由用户显式配置，已知平台推断只能作为默认值。
- Kimi 原生 `capabilities/default_effort/adaptive_thinking/beta_api` 与代理统一能力模型双向映射。
- Responses -> Chat/Anthropic/Google 转换必须保留工具调用关联、推理摘要和流式 usage。
- 不把 `reasoning_content` 回灌成用户可见正文，不记录私有原始 CoT；日志只记录 token 数和公开响应字段。
- thinking 整流器的作用边界要区分“协议合法化”和“更改用户推理意图”，后者禁止静默发生。

### R7. 设置与状态 UI（P0）

- 本地路由接管区显示 Kimi Code 开关与正确标签。
- 故障转移设置增加 Kimi Code tab，包含队列排序、启停、重试、超时、熔断配置。
- 状态 hook、停止保护、托盘菜单和活动目标展示包含 Kimi。
- 不支持的 provider 协议（若 Q1 选择分期）必须在开启接管前阻断，并明确指出类型，不能运行后 404/500。

### R8. 使用统计（P0）

- 前端 `AppType`、筛选按钮、图标、四种语言文案包含 Kimi Code。
- 请求日志、日汇总、趋势、供应商统计、模型统计支持 `kimicode`。
- Kimi Responses/Chat/Anthropic/Google 流式与非流式 usage 统一归一化。
- Kimi 输入 token 缓存语义按实际入站/上游协议定义，不直接沿用 Codex 字符串判断。
- 请求统计与官方账户额度分离：请求统计展示 token、缓存、延迟、模型和可验证成本；官方 OAuth 额度展示 5 小时/周窗口、重置时间和 Extra Usage 余额，不把订阅额度折算成虚假的每 token 成本。
- 官方 OAuth token、API key、请求头不得进入统计明细。

### R9. 与上游 cc-switch 合并（P1）

- Kimi fork 改动集中在小型适配层和显式枚举扩展，避免复制整套 Claude/Codex handler。
- 数据库使用 fork migration marker；不抢占上游 schema 版本。
- 保持旧 `hermes` 数据读取兼容，但新写入统一使用 `kimicode`。
- 建立上游合并检查表：schema、AppType match、路由表、i18n、usage 类型、provider seed。

## 5. 验收标准

### AC1. 新旧数据库

- 内存新库和从 v3.17.2 数据库升级后均可读写 `get_proxy_config_for_app("kimicode")`。
- 旧四行配置值逐字段不变，Kimi 行只出现一次。

### AC2. 无损接管

- 给定包含注释、`thinking/hooks/permission/mcp` 和多个 provider 的配置，开启再关闭接管后语义等价；无 CC Switch 占位项残留。
- 强制终止后重启能够恢复，Kimi Code 0.26.0 不指向已停止端口。

### AC3. 请求闭环

- Kimi Code 0.26.0 真实 smoke test 覆盖官方 OAuth、OpenAI Chat、OpenAI Responses、Anthropic、Google GenAI 和 Vertex AI。
- 每种协议各覆盖一次流式文本、工具调用、thinking、上游 429 后故障转移。

### AC4. 导入与热切换

- 导入现有 Kimi 配置后 provider/model/default 引用完整。
- 接管中切 P1/P2 不改写真实上游地址到 Live；关闭后恢复为最后选择的 provider。
- 保存失败回滚数据库 current、恢复备份和 Live 占位配置三者。

### AC5. 统计

- Kimi 请求在“全部”和“Kimi Code”筛选下出现，在 Codex 筛选下不出现。
- 映射前模型、上游模型、计价模型分别正确；流式 usage 不为无故全零。

### AC6. 质量门禁

- Rust 单元/集成测试、前端 Vitest、TypeScript、Prettier、Clippy/format 全部通过。
- Windows MSI 从干净工作区构建成功；仍为未签名包时明确标注“未知发布者”。
- 在用户批准前不上传 GitHub、不创建 tag、不发布 Release。

## 6. 非目标

- 不展示或存储模型私有原始思维链。
- 不把 Kimi Code 改造成新的通用代理产品；只扩展现有 CC Switch 路由架构。
- 本任务不解决 Authenticode 证书缺失；未签名发布策略保持现状。
- 不在功能完成前更改版本号或发布资产。

## 7. 已确认产品决策

- 首版一次支持全部六种 Kimi 原生 provider 类型：`kimi`、`openai`、`openai_responses`、`anthropic`、`google-genai`、`vertexai`。
- 本机 `kimi --version` 的产品线是 `MoonshotAI/kimi-code`，首要兼容基线固定为 tag `@moonshot-ai/kimi-code@0.26.0`（commit `36b05820cba24e09fdff19a059afc08ccea2c35e`）。协议、OAuth、thinking、工具调用和流式行为的实现与测试必须绑定到该 commit。
- `MoonshotAI/kimi-cli` 的 Python 版 `1.49.0` 是另一套 CLI 产品线，只能作为前瞻性协议对照，不能替代本机 Kimi Code 0.26.0 的行为基线。
- 官方 OAuth 失败时不静默切换第三方 provider；官方实现只对同一 provider 强制刷新一次，随后返回认证错误。
- 托管 provider 采用字段级所有权：登录/登出是唯一管理入口，官方字段只读，用户 alias/overrides 等扩展保留。
- Kimi Live 固定使用 `openai_responses` 连接 CC Switch 本地入口；代理内部每个 attempt 仍按目标 provider/model 的正式 `type`/`protocol` 转换到真实上游，不把 Responses 当作所有上游协议。
- 导入采用 source-aware merge；同源刷新同步新增/更新/删除并保留用户字段，异源同 ID 必须显式解决。
- 使用统计把请求级 token/成本与官方订阅额度分栏展示；托管 OAuth 不推算每 token 费用。
- 不接受“UI 可保存但代理不支持”的降级方案。
