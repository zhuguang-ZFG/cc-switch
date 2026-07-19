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

## 第二轮：对照 Kimi Code CLI 0.27 开源实现的契约审查

三个并行审查代理逐契约比对 `.vendor/kimi-code-0.27.0`（OAuth 生命周期 / config.toml 契约 / 用量数据契约）。

### 验证匹配（勿再报）

- 凭据文件格式与 CLI `TokenInfoWire` 字节级兼容（snake_case、`expires_at` 秒、默认值一致），路径一致。
- 刷新阈值 `max(300, expires_in/2)` 与 CLI `defaultRefreshThreshold` 一致。
- 六种 provider `type` 拼写与 CLI `ProviderTypeSchema` 完全一致（真正的校验器是 agent-core schema.ts，不是 custom-registry.ts）。
- `max_context_size` 必填兜底、`${provider}/${id}` 别名推导、`managed:` + `oauth/kimi-code` 受管约定、接管用 `openai_responses` 指向 localhost——全部匹配。
- usage.record 四个 token 字段名与写入端一致；每条事件是单次 LLM 调用的增量（非累积快照），`turn`/`session` scope 不重叠，不存在重复计数。
- 会话目录布局（`sessions/<hash>/<id>/agents/<agent>/wire.jsonl`）与 resume `--session <id>` 匹配。

### 已修复（第二轮，见 CHANGELOG）

1. **[blocker] 跨进程并发刷新互踩**：两工具锁路径不同（CLI 在 Windows 干脆无锁），输的一方拿 invalid_grant 后 CLI 会写"注销墓碑"覆盖轮换后的好 token → 双双登出。cc-switch 现在在刷新遭 401/invalid_grant 后重读凭据文件、采纳对端轮换的 token（镜像 oauth-manager.ts:369-383）；icacls 仅首建时执行以缩小窗口。
2. 刷新对 429/5xx/传输错误 3 次指数退避重试（镜像 oauth.ts RETRYABLE_STATUSES）。
3. 设备流 `slow_down` 前后端联动升频 +5s（RFC 8628 §3.5）。
4. managed 目录 `protocol` 仅在恰为 `anthropic` 时写入（CLI zod literal，写其它值会整条模型被丢弃）。
5. 受管判定收窄到 `managed:` 前缀 / `oauth.key == "oauth/kimi-code"`（原先任何带 oauth 表的自定义 provider 都被锁死）。
6. Kimi booster 钱包 → `extra_usage`（月度上限/已用/占比/币种，1e6 定点分转换同 CLI）。
7. `/usages` 相对重置（`reset_in`/`ttl` 秒）转绝对 RFC3339 时间。
8. 自定义模型 `name` → `display_name`；补种子行裸 `kimi-k2`（原落到 kimi-for-coding 估算价）。
9. 非标准 wire.jsonl 布局整体跳过（避免 bucket 哈希误标为 session_id、request_id 去重键冲突）。

### 未修（记录在案）

- vertexai / google-genai 类型在表单中无 `env` 字段（`GOOGLE_CLOUD_PROJECT` 等），选它们会得到运行时失败的 provider——需要表单支持 `env`/`custom_headers`，UI 工作量较大。
- `max_output_size`/`reasoning_key` 未在自定义 provider 表单中暴露（CLI 可选字段，无拒绝风险）。
- 设备流 `expired_token` 是硬错误而非自动重发新码（CLI 会无缝重来）；前端已有过期计时，仅 UX 差异。
- `x-msh-os-version` 发送的是 OS 名而非版本号（CLI 发 `os.release()`），纯遥测字段，服务端不校验。

## 后续跟进

- `deeplink/tests.rs` 的 `TestHomeGuard` 改 `HOME`/`USERPROFILE` 但 31 测试仅 1 个 `#[serial]`——目前未见失败，若出现随机失败按 kimi_config 同法处理。
- Coding Plan 订阅价目为估算价（schema.rs 种子有 ESTIMATE ONLY 注释），Moonshot 公布正式价后替换。
- 上表"未修"四项按需求排期。
