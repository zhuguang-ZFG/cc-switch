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

## 第三轮：对抗性复审 + 流式热路径 + 故障转移并发 + 性能扫描

四路并行：对第二轮修复本身做对抗复审、代理流式转换热路径、故障转移引擎并发正确性、全局性能。

### 已修复（第三轮）

- **[blocker] 半开探测名额泄漏**：客户端中途断连 drop 掉持有探测名额的 future，名额永不归还 → provider 被永久跳过。熔断器现在会回收超过 300s 未归还的陈旧名额（circuit_breaker.rs，附两个单测）。
- **[blocker] 同步关闭接管绕过切换锁**：`disable_takeover_for_app_sync`（profile apply 路径）现在与其他 Live 写入者共用 per-app 切换锁；故障转移热切换换用 `hot_switch_provider_for_failover`，拿锁后复查 enabled，杜绝迟到切换偷改当前供应商 SSOT。
- **关闭接管时同步复位内存熔断器**（两条 disable 路径），与 DB 健康清理同源。
- **Chat→Responses 流式转换**：补 EOF 哨兵（上游漏发结尾空行时不再丢最后一个事件/usage）+ JSON 文档回退（网关无视 stream:true 时不再报 stream_truncated）。
- **OAuth 免锁快路径**：token 未临期时不再取全局互斥 + 跨进程锁文件（原来每个 Kimi 请求都要建/删锁文件 + 起心跳线程，并发流量全部串行）。
- **[对抗复审抓到的回归] legacy wire.jsonl 布局**：第二轮的 agents 目录 guard 比官方 CLI 更严（CLI 的 wire-scan 同时读 v1 会话根布局），会静默丢弃老会话用量——已改为双布局解析。
- 对抗复审其余项：真撤销写 CLI 同款墓碑（两工具停止复用死 token）；booster `is_enabled` 默认改 false（对齐 CLI `=== true`）；锁等待期限 30s→100s 覆盖持有方重试窗口；`real_oauth_refresh_smoke` 补 `#[serial]`；注释漂移修正。
- **性能**：SQLite 开 WAL + synchronous=NORMAL + busy_timeout(5s)；6 个仪表盘聚合命令 + 会话同步改 async + spawn_blocking（原先跑主线程、与代理写日志抢全局连接锁）；usage 事件去抖 200ms→1.5s；thinking delta O(n²) 累积修复。
- **表单补全（原"未修"四项全部落地）**：vertexai 显示 GOOGLE_CLOUD_PROJECT/LOCATION env 字段；模型暴露 max_output_size；设备码过期自动换新码重试（限 2 次）；`x-msh-os-version`/`device-model` 携带真实 OS 版本号。

### 已知未修（按需排期）

- 故障转移无退避/抖动：熔断开启前的突发请求会紧凑打满队列（W3）；并发故障转移最终当前供应商是完成序决定（W4）；`TransformError` 一刀切 Retryable 可能让畸形客户端请求毒化所有熔断器（W2，需拆错误类型）。
- Gemini 流式：无 finishReason 的干净截断被当正常完成（W3）；Claude 路径中途错误裸断连（W4）。
- 性能后续：启动阻塞的 rollup/vacuum 挪后台（可挂 24h 维护定时器）；会话同步按字节 offset Seek 而非行数重读；前端零代码分割（recharts/CodeMirror 全量进首包）；2s 代理状态轮询三连可合并。
- vertexai/google-genai 仍无 custom_headers 表单字段。

## 第四轮：外部输入安全 + 同步/密钥 + profile 系统 + 对抗复审

四路并行：deeplink 导入攻击面、S3/WebDAV 同步与密钥处理、profile 跨应用同步、对抗复审第三轮修复。

### 已修复（第四轮）

安全面：
- **[blocker] deeplink MCP 导入 = 命令执行但确认框不完整**：原先只显示 `command`（`bash -c "curl|sh"` 只显示 "bash"）。现在完整展示 command+args+env，并对 stdio 服务器加执行警告。
- **deeplink 提示词导入**：预览不再截断 500 字符（藏匿注入指令），并警告会覆盖 live 提示词文件。
- **[HIGH] 同步端点拒绝公网 http**：同步产物是含全量 API key 的明文 SQL 转储，WebDAV/S3 的 http 仅放行回环/私网/.local。
- **settings.json Windows ACL**：首次创建时设为当前用户独占（存着 S3/WebDAV 明文凭据）。
- **zip-slip / 路径穿越**：skill `repo` 段与压缩包条目拒绝 `..`/绝对路径；base64 载荷上限 512KB；config 载荷里的 endpoint/homepage 补 http(s) 校验。

profile/流式/韧性：
- **[blocker] MCP toggle 先写 live 后落 DB**（对齐 skill）：live 写失败不再留下 DB 已翻转、profile 重复 apply 修不回来的状态。
- profile apply 加全局串行锁；Kimi 接管期间快照回退到 DB 当前供应商（否则永久抹除旧 profile 的供应商槽）。
- 对抗复审抓到的第三轮问题：Chat→Responses JSON 回退不再吞掉 JSON+SSE 混合响应（解析失败回落 SSE）+ 8MB 上限；Gemini 无 finishReason 映射为 incomplete + 中途错误发结构化 error 事件；熔断器陈旧探测时间戳在释放/转半开时清除（消除并发第二探测竞态）；wire.jsonl legacy 布局重新锚定精确深度（第三轮放宽重开了 hash-as-session-id）；OAuth 墓碑写入错误不再静默丢弃；设备码续期防双触发。

### 已知未修（需产品决策/较大工作量）

- 同步无客户端加密选项 + 恢复无 provider/base_url diff 预览（MITM/被篡改的 bucket 可劫持 base_url 偷密钥）——建议后续加 diff 确认与可选 passphrase 加密。
- profile：部分失败仍无条件设 current marker（无 dirty 标记）；profile 级别无 MCP live-file 写锁（apply 与另一窗口 MCP toggle 可丢更新）；前端编辑对话框 RMW 可回写陈旧 apps 标志。
- 熔断器：僵尸探测（>300s 存活探测在回收后仍能影响熔断状态 / 双减名额）为有界的接受取舍，若要彻底解决需给 permit 打 epoch 标记。
- deeplink：CSP `connect-src` 放行任意 http(s) 主机（XSS 时的外泄通道）；`deplink.html` 建议排除出发布产物。
- 故障转移无退避/抖动；`TransformError` 一刀切 Retryable（畸形客户端请求可毒化全部熔断器，需拆错误类型）——第三轮已记录，仍未做。

## 后续跟进

- `deeplink/tests.rs` 的 `TestHomeGuard` 改 `HOME`/`USERPROFILE` 但 31 测试仅 1 个 `#[serial]`——目前未见失败，若出现随机失败按 kimi_config 同法处理。
- Coding Plan 订阅价目为估算价（schema.rs 种子有 ESTIMATE ONLY 注释），Moonshot 公布正式价后替换。
- 上表"未修"四项按需求排期。
