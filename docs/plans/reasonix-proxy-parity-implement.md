# Reasonix 代理接管与故障路由对齐执行计划

状态：**Implemented**（`feat/reasonix-app`，commit 含应用接入 + 代理闭环）  
对照：`docs/plans/reasonix-proxy-parity-design.md`、`docs/plans/reasonix-proxy-parity-prd.md`  
对照实现（只读参考）：Kimi / Grok 的 takeover + failover

## 0. 实施纪律

- [x] 实现前通读 design §1–§7、§13 与 PRD §4–§5。
- [x] 触碰 live 文件的测试使用临时目录 fixture（`REASONIX_HOME`）。
- [x] 上游 `ccswitch.go` 以 vendor 补丁形式落地（非直接改远端仓库）。
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
- [x] **真实 CLI**：`reasonix_cli_run_hits_local_proxy_chat_ingress` — 本机 `reasonix run` → `/reasonix/v1` → remap → mock upstream（流式 SSE）
- [x] Claude/Codex/Kimi/Grok 既有路径未改契约（Reasonix 独立 namespace）

> 说明：当前 **已安装的** `cc-switch.exe` 尚无 `/reasonix/v1`（404）；真实入口需用本分支构建启动。上述 e2e 用本分支 in-process 代理 + 系统 PATH 上的 Reasonix CLI 验证。

## 8. 产品边界（已闭环，非缺口）

下列项**不是待办**，已按 1A/2A/3A 决策关闭，勿再列为 Reasonix 补齐项：

| 项 | 闭环结论 | 理由 |
|----|----------|------|
| Reasonix OAuth / 托管登录 | **N/A** | 上游 Reasonix 无对等 OAuth 契约；不虚构 managed 登录 |
| Responses 入站（`/reasonix/v1/responses`） | **不适用** | Live `kind=openai` 固定 Chat Completions；CLI 只拼 `/chat/completions` |
| OpenCode / OpenClaw 代理接管 | **范围外** | 独立产品能力；若要做另开分支（如 `feat/opencode-openclaw-proxy`），不在本 Reasonix 闭环内 |

### 已补齐的前序延后项

- [x] Live→DB 导入（`import_reasonix_providers_from_live` + 启动同步）
- [x] Deeplink / MCP apps 白名单放行 `reasonix`
- [x] `chat_url` 出站透传 + 表单 `chat_url`/`models_url` 高级字段
- [x] Universal 表单 Reasonix 开关与同步
- [x] JSON SSOT 占位检测；接管备份删除清 `.env`
- [x] 上游 `ccswitch.go` vendor 补丁（见 `docs/patches/reasonix-ccswitch-enabled-reasonix.md`；待上游合入）

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
