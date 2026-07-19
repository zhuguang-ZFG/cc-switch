# Kimi 集成审查记录（2026-07-20）

对 `98c22e86..8a08baea`（10 个 Kimi Code 提交，173 文件，+13k/−8k）的三路并行深度审查结论与修复清单。

## 审查方法

- 三个并行审查代理：Rust 代理/路由/OAuth 核心、Rust 服务与数据层、React/TS 前端。
- 验证基线：`cargo check --all-targets`、`cargo test --lib`、`pnpm typecheck`、`pnpm test:unit` 全绿后开始。
- 每个 warning 级发现都经过主线程二次复核（读源码确认）后才定级。

## 结论

**无正确性或安全阻塞项。** 以下声明经验证属实：

| 声明 | 验证依据 |
| --- | --- |
| fail-closed 路由（8a08baea） | `apply_kimi_upstream_model` 对未映射 placeholder 返回 `ConfigError`，`categorize_proxy_error` 归类 Retryable 推进 failover，不放行死别名 |
| fork 迁移幂等 | `fork_migration_kimicode_v1` 标记 + SAVEPOINT 回滚 + 双跑测试；rollup 复合主键做加法合并而非 `UPDATE OR IGNORE` |
| serde 无损回写 | `rename="kimicode", alias="hermes"` 贯穿 McpApps/SkillApps/VisibleApps 等；旧配置读入后以新键写出，无字段丢失 |
| 会话文件读取防穿越 | `canonicalize()` + `starts_with(root)` + 文件名匹配 session id，有 `../../` 用例覆盖 |
| 无 secrets 入日志 | token JSON 以 0600/icacls 权限落盘，日志仅含错误分类 |

## 已修复（见 CHANGELOG Unreleased）

1. OAuth 刷新锁心跳线程 15s 不可中断 sleep → mpsc `recv_timeout`，Drop 即时唤醒。
2. `save_oauth_token`（Windows `icacls` 同步子进程）→ `spawn_blocking`。
3. `verification_uri_complete` 加 `#[serde(default)]`（RFC 8628 可选字段）。
4. 请求体 model 拼 URL 前经 `sanitize_gemini_model_for_path` 校验（拒 `..`/`.`/空段/元字符）。
5. 遗留 Hermes 编辑兼容 `api_mode`→`type`、`context_length`→`max_context_size`。
6. `insert_kimi_session_entry` 返回真实插入结果，并发下计数不虚高。
7. `vitest.config.ts` 加 `include`，`pnpm test:unit` 不再吞 `.vendor` 第三方测试。
8. `kimi_config` 测试改用全局 `#[serial]`，消除与其他模块 env 竞争导致的并行 flakiness（毒化级联）。
9. 删除 ProviderForm/lib.rs 中不可达的 Gemini 死代码。
10. `test_model_pricing_matching` 的 `kimi-for-coding` 断言与估算价种子对齐。

## 已知非缺陷（设计决定，勿再报）

- 未知**非 placeholder** 模型直接透传上游：文档化的设计。
- 受管 Kimi 在混合队列首位且刷新后仍 401 时中止整个队列：与 codex-official 策略一致。
- gemini 迁移后 DB 留有孤儿 provider 行：产品移除 Gemini CLI 的既定行为，无代码路径读取。
- `mcp_servers`/`skills` 表保留 `enabled_hermes` 列名：DAO 映射一致，无需迁移。

## 后续跟进

- `deeplink/tests.rs` 的 `TestHomeGuard` 改 `HOME`/`USERPROFILE` 但 31 测试仅 1 个 `#[serial]`——目前未见失败，若出现随机失败按 kimi_config 同法处理。
- Coding Plan 订阅价目为估算价（schema.rs 种子有 ESTIMATE ONLY 注释），Moonshot 公布正式价后替换。
