# Reasonix 代理接管与故障路由对齐执行计划

状态：**Implemented**（`feat/reasonix-app`，commit 含应用接入 + 代理闭环）  
对照：`docs/plans/reasonix-proxy-parity-design.md`、`docs/plans/reasonix-proxy-parity-prd.md`  
对照实现（只读参考）：Kimi / Grok 的 takeover + failover

## 0. 实施纪律

- [x] 实现前通读 design §1–§7、§13 与 PRD §4–§5。
- [x] 触碰 live 文件的测试使用临时目录 fixture（`REASONIX_HOME`）。
- [x] 未修改上游 Reasonix `internal/config/ccswitch.go`。
- [x] 未复用 `/kimicode/v1/responses`；Reasonix 入站为 Chat。

## 1. 阶段 A：基线与回归护栏

- [x] 分支具备累加供应商 / MCP / UI。
- [x] 代理支持 app 列表、故障转移 UI、usage AppType 含 `reasonix`（含 `PROXY_STARTUP_APP_TYPES` 断言）。
- [x] 隔离 fixture：多 `[[providers]]`、`.env`、未知字段（takeover / hot-switch smoke）。

## 2. 阶段 B：数据库

- [x] 新库 `proxy_config` CHECK 加 `reasonix` 并 seed。
- [x] `fork_migration_reasonix_proxy_v1`（重建 / INSERT / marker）。
- [x] DAO 全局 listen·port·log 镜像覆盖 reasonix 行。
- [x] 测试：新库、旧库（含 kimicode-only rebuild）、双跑、旧行字段保持。

## 3. 阶段 C：Reasonix 配置原语（takeover）

- [x] `apply_proxy_takeover`：`cc-switch-proxy` / `kind=openai` / `/reasonix/v1` / `cc-switch-proxy-default` / `no_proxy` / 清 `models_url`·`chat_url` / `default_model=cc-switch-proxy`
- [x] `is_proxy_takeover_active`（要求 `/reasonix/v1` + env 占位）
- [x] `clear_proxy_takeover` / env 清理
- [x] `.env`：`PROXY_MANAGED`；关闭时**还原**接管前旧值（sidecar `.cc-switch-proxy-api-key.bak`），无旧值则删除
- [x] 幂等 / 端口匹配检测单测

## 4. 阶段 D：ProxyService 接管 / 恢复 / 热切换

- [x] `ProxyTakeoverStatus.reasonix`
- [x] backup / apply / restore backup / SSOT / cleanup
- [x] `stop_proxy_server` 拦截 reasonix；crash recovery / `PROXY_STARTUP` 含 reasonix
- [x] `set_takeover_for_app(Reasonix)` 共用启停编排
- [x] 热切换：Live 保持代理；备份更新 provider id `default_model`
- [x] 单测：端口变更重建 Live URL；半接管 crash recovery

## 5. 阶段 E：代理路由与模型映射

- [x] `POST /reasonix/v1/chat/completions`、`GET /reasonix/v1/models`
- [x] handler 固定 `app_type=reasonix`
- [x] `apply_reasonix_upstream_model` fail-closed
- [x] `openai` → Chat；`anthropic` → Chat→Messages + 真流式 SSE
- [x] forwarder remap 接线
- [x] 单测：remap；P1 openai 失败 → P2 anthropic 协议转换成功（`forward_with_retry`）

## 6. 阶段 F：前端与 i18n

- [x] 接管开关 / 状态含 reasonix
- [x] 故障转移队列编辑含 reasonix
- [x] 用量 / 定价筛选含 reasonix
- [x] `en` / `zh` / `zh-TW` / `ja`
- [x] `proxy_mode=custom` / `no_proxy` 风险提示（Reasonix 接管开启时）

## 7. 阶段 G：验收与回归

- [x] design §14.1 单元项（takeover / remap / migration / hot-switch / port rebuild / crash / cross-kind failover）
- [x] `cargo test --lib reasonix_` + migration / forwarder failover
- [x] `cargo check --lib`
- [x] `pnpm typecheck`
- [ ] **可选手动**：本机安装 Reasonix CLI 后跑一条真实 chat（自动化已覆盖协议与恢复路径）
- [x] Claude/Codex/Kimi/Grok 既有路径未改契约（Reasonix 独立 namespace）

## 8. 明确非目标（已跳过）

- Reasonix OAuth
- Responses 入站
- 修改上游 `ccswitch.go` 的 `enabled_codex` → `enabled_reasonix`
- OpenCode / OpenClaw 代理
- 完整 live→DB 导入（SSOT 以 DB + 备份为准）

## 9. 关键契约速查

```text
Live base_url  = http://127.0.0.1:<port>/reasonix/v1
CLI 实际请求  = POST {base_url}/chat/completions
路由注册      = POST /reasonix/v1/chat/completions

占位 provider = cc-switch-proxy
占位 model    = cc-switch-proxy-default
.env          = CC_SWITCH_PROXY_API_KEY=PROXY_MANAGED
               (+ .cc-switch-proxy-api-key.bak 保存旧值)
kind          = openai（Live 固定）
no_proxy      = true（custom 全局代理模式下可能无效 → UI 提示）
```

## 10. 文档索引

| 文档 | 用途 |
|------|------|
| `docs/plans/reasonix-proxy-parity-prd.md` | 产品目标、验收、风险 |
| `docs/plans/reasonix-proxy-parity-design.md` | 详细技术设计与源码证据 |
| `docs/plans/reasonix-proxy-parity-implement.md` | 本执行清单 |
| `docs/plans/kimi-parity-design.md` | 结构与闭环对照（Responses≠Chat） |
