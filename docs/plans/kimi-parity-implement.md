# Kimi Code 功能对齐执行计划

状态：Scope confirmed，已绑定 Kimi Code 0.26.0 基线，等待最终设计批准后开始

## 0. 实施纪律

- 计划批准前不改产品代码。
- 每一阶段先补失败测试，再实现，再运行该层测试。
- P0 未全部通过不得增加版本号、构建发布资产或上传 GitHub。
- 所有真实 Kimi smoke test 使用隔离 `KIMI_CODE_HOME`；不得覆盖用户当前配置。

## 1. 阶段 A：基线与回归护栏

- [ ] 记录干净基线、当前完整测试数量和构建命令。
- [ ] 添加 Kimi parity matrix 测试清单。
- [ ] 添加静态测试：代理支持列表、UI tab、usage AppType 必须包含同一组应用。
- [ ] 建立隔离 Kimi 配置 fixture：多 provider、官方 OAuth 引用、thinking/hooks/MCP、注释、未知字段。

完成门禁：只新增测试/fixture，测试应准确暴露当前缺口。

## 2. 阶段 B：数据库

- [ ] 新库 CHECK 加 `kimicode` 并 seed 默认行。
- [ ] 实现 fork migration 重建旧 `proxy_config`。
- [ ] DAO 初始化、默认成本倍率、计价模式和全局镜像覆盖 Kimi。
- [ ] 增加新库、旧库、已跑 v1 fork migration、重复启动幂等测试。

重点文件：

- `src-tauri/src/database/schema.rs`
- `src-tauri/src/database/dao/proxy.rs`
- `src-tauri/src/database/tests.rs`

完成门禁：数据库测试全绿，旧行逐字段保持。

## 3. 阶段 C：Kimi 配置原语

- [ ] 增加完整 TOML snapshot/read/write API。
- [ ] 增加纯函数：apply takeover、detect takeover、cleanup takeover、set default provider in snapshot。
- [ ] 把 provider upsert 抽成 document-level 函数，Live 写入与备份更新复用。
- [ ] 验证注释/未知表保留、托管 provider 不被覆盖、重复执行幂等。

重点文件：

- `src-tauri/src/kimi_config.rs`
- `src-tauri/tests/kimi_roundtrip.rs`

完成门禁：所有转换先在内存字符串完成，失败不触碰用户文件。

## 4. 阶段 D：导入供应商与标准投影

- [ ] 定义 provider type -> wire/auth 映射。
- [ ] 导入时校验 provider/model/default 引用。
- [ ] 生成模型与 reasoning capability 投影。
- [ ] 接管期间 create/update/delete 改写恢复备份，不改写代理 Live。
- [ ] 修复仍按旧 Hermes `apiKey/baseUrl` 读取 Kimi 的遗留路径。
- [ ] 添加重复导入、冲突、托管只读、非法引用、未知字段测试。

重点文件：

- `src-tauri/src/services/provider/mod.rs`
- `src-tauri/src/services/provider/live.rs`
- `src-tauri/src/provider.rs`
- `src-tauri/src/kimi_config.rs`

完成门禁：导入前后原生配置语义等价，数据库投影确定。

## 5. 阶段 E：接管、恢复与热切换

- [ ] 后端 takeover status 增加 Kimi。
- [ ] stop protection、crash recovery、keep-state restore、全量备份循环增加 Kimi。
- [ ] `ProxyService` 增加 Kimi read/write/backup/takeover/restore/detect/cleanup。
- [ ] `ProviderService::switch` 移除 Kimi 提前绕过，接入 per-app lock/hot switch。
- [ ] 热切换原子更新 current + backup + active target，失败完整回滚。
- [ ] 端口变化、半接管、缺备份和重复开关测试。

重点文件：

- `src-tauri/src/services/proxy.rs`
- `src-tauri/src/services/provider/mod.rs`
- `src-tauri/src/commands/proxy.rs`
- `src-tauri/src/proxy/types.rs`

完成门禁：任何停止/崩溃场景都不会留下本地死地址。

## 6. 阶段 F：协议与 OAuth

- [ ] 参数化 Responses handler，增加 Kimi namespace。
- [ ] Kimi provider 每个 attempt 动态选择 adapter。
- [ ] 补齐模型 alias -> upstream model 映射。
- [ ] 接入 OpenAI Chat、Responses、Anthropic 转换。
- [ ] 实现 Responses 与 Google GenAI/Vertex 的双向流式转换。
- [ ] 按 Kimi Code 0.26.0 的六值枚举选择 adapter；仅迁移历史 CC Switch 别名，不把 Python CLI 内部名称写回 Kimi 配置。
- [ ] 抽取 Kimi OAuth refresh manager，处理设备头、进程内刷新合并、Windows 服务锁/原子写入与单次 401 重试；POSIX 可启用跨进程文件锁。
- [ ] 添加混合协议 P1/P2 故障转移测试。

重点文件：

- `src-tauri/src/proxy/server.rs`
- `src-tauri/src/proxy/handlers.rs`
- `src-tauri/src/proxy/forwarder.rs`
- `src-tauri/src/proxy/providers/*`
- `src-tauri/src/commands/auth.rs`
- `src-tauri/src/services/subscription.rs`

完成门禁：真实 Kimi Code 0.26.0 smoke matrix 通过，Codex/Grok Build 原测试无回归。

## 7. 阶段 G：思维/推理语义

- [ ] 增加统一 ReasoningCapabilities 类型和序列化契约。
- [ ] 官方 models API 能力映射与自定义表单显式配置对齐。
- [ ] 每种协议验证 thinking on/off、effort、always-thinking。
- [ ] 流式 reasoning + tool call 顺序和 ID round-trip 测试。
- [ ] 验证 `ThinkPart.encrypted`、Anthropic signature 和 Google `thought_signature_b64` 的 round-trip。
- [ ] 确认日志/错误不泄露私有 CoT、token、密钥。

重点文件：

- `src-tauri/src/provider.rs`
- `src-tauri/src/proxy/providers/codex.rs`
- `src-tauri/src/proxy/providers/transform_*`
- `src/components/providers/forms/HermesFormFields.tsx`
- `src/components/providers/forms/ProviderForm.tsx`

完成门禁：显式配置胜过推断，关闭 thinking 不被代理静默打开。

## 8. 阶段 H：设置、状态和使用统计

- [ ] 路由接管区增加 Kimi。
- [ ] 故障转移 tab/队列/熔断设置增加 Kimi。
- [ ] hooks、types、托盘、active targets 和停止状态加入 Kimi。
- [ ] Usage AppType、筛选图标和四语文案加入 Kimi。
- [ ] usage parser/logger/cache semantics 支持 Kimi 各协议。
- [ ] Dashboard、请求明细和 rollup 回归测试。

重点文件：

- `src/components/settings/ProxyTabContent.tsx`
- `src/components/proxy/ProxyPanel.tsx`
- `src/hooks/useProxyStatus.ts`
- `src/types/proxy.ts`
- `src/types/usage.ts`
- `src/components/usage/UsageDashboard.tsx`
- `src/i18n/locales/*.json`
- `src-tauri/src/proxy/usage/*`
- `src-tauri/src/services/usage_stats.rs`

完成门禁：Kimi 数据不出现在 Codex 桶，四种语言无缺 key。

## 9. 阶段 I：深度回归与安全审计

- [ ] `cargo fmt --check`
- [ ] `cargo clippy --all-targets --all-features -- -D warnings`
- [ ] `cargo test --lib`
- [ ] `cargo test --tests`
- [ ] `pnpm typecheck`
- [ ] `pnpm format:check`
- [ ] `pnpm test:unit`
- [ ] `pnpm build:renderer`
- [ ] 密钥/日志扫描、备份清理、异常退出恢复测试。
- [ ] Claude/Codex/Grok Build 路由、切换、usage 回归矩阵。
- [ ] 从 v3.17.2 已安装数据目录执行升级演练和降级恢复演练。

完成门禁：零失败、零跳过的 P0 验收项；已知限制必须在 PRD 中明确且用户批准。

## 10. 阶段 J：构建与发布（需再次授权）

- [ ] 仅在 I 阶段完成后更新版本号和 release notes。
- [ ] 构建未签名 MSI/NSIS（按当前发布决策）。
- [ ] 在干净 Windows 环境安装、覆盖安装、卸载/保留数据验证。
- [ ] 计算 SHA256 并记录“未知发布者”预期行为。
- [ ] 用户验收通过后才 commit/push/tag/release。

## 11. 主线同步检查表

每次合并上游 cc-switch：

- [ ] 先建立临时合并分支并记录上游基线。
- [ ] 检查 `AppType` 新增/删除和所有 match arms。
- [ ] 检查数据库 schema/migration 是否改变 `proxy_config`。
- [ ] 检查 proxy handler/forwarder/usage parser 是否重构。
- [ ] 检查 provider meta、reasoning 和模型目录契约变化。
- [ ] 检查前端应用列表、设置 tab、i18n key 变化。
- [ ] 运行完整 parity matrix 后再合入 main。

## 12. 预估风险顺序

1. 混合协议流式转换和工具调用连续性。
2. Kimi OAuth 并发刷新与认证失败故障转移策略。
3. additive TOML 在接管期间的新增/删除 provider 原子性。
4. SQLite CHECK 重建迁移对旧库列完整性的影响。
5. usage 缓存 token 语义被错误沿用 Codex。

每个高风险点必须有失败注入测试，不接受只靠手工验证。
