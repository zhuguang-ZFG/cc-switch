# Reasonix 代理接管与故障路由对齐执行计划

状态：Design approved —— 可由实现 agent 按阶段执行  
对照：`docs/plans/reasonix-proxy-parity-design.md`、`docs/plans/reasonix-proxy-parity-prd.md`  
对照实现（只读参考，勿盲抄路径）：Kimi / Grok 的 takeover + failover

## 0. 实施纪律

- 实现前通读 design §1–§7、§13 与 PRD §4–§5。
- 每一阶段：先补失败测试 → 再实现 → 再跑该层测试。
- P0 未全部通过不得发版。
- 所有触碰 live 文件的测试使用**临时目录** fixture，不得覆盖用户 `%APPDATA%\reasonix`。
- Reasonix 源码只读副本：`.vendor/DeepSeek-Reasonix`（若缺失则 `git clone` 到该路径；已在 `.gitignore`）。
- **不要**修改上游 Reasonix 的 `internal/config/ccswitch.go`（MCP 启用位对齐另开任务）。
- **不要**复用 `/kimicode/v1/responses`；Reasonix 入站是 Chat。

## 1. 阶段 A：基线与回归护栏

- [ ] 确认分支 `feat/reasonix-app` 上已有累加供应商 / MCP / UI（代理尚未做）。
- [ ] 添加静态断言或清单测试：代理支持 app 列表、故障转移 UI、usage AppType 最终须含同一组（含 `reasonix`）——可先以 `#[ignore]` / 失败测试暴露缺口。
- [ ] 建立隔离 fixture：多 `[[providers]]`、`[[plugins]]`、`.env`、未知字段、注释。

完成门禁：只新增测试/fixture，准确暴露当前缺口。

## 2. 阶段 B：数据库

- [ ] 新库 `proxy_config` CHECK 加 `reasonix` 并 seed 默认行。
- [ ] 实现 `fork_migration_reasonix_proxy_v1`（对齐 `fork_migration_kimicode_proxy_v1`）：重建表、复制旧行、INSERT reasonix、写 marker。
- [ ] DAO / 全局 listen·port·log 镜像 UPDATE 覆盖 reasonix 行。
- [ ] 测试：新库、旧库、双跑 marker、旧行字段保持。

重点文件：

- `src-tauri/src/database/schema.rs`
- `src-tauri/src/database/dao/proxy.rs`（若有）
- `src-tauri/src/database/tests.rs`

完成门禁：数据库相关测试全绿。

## 3. 阶段 C：Reasonix 配置原语（takeover）

在 `reasonix_config.rs`（或并列模块）增加纯函数，内存 Document 优先：

- [ ] `apply_proxy_takeover(proxy_base_url, api_key_placeholder) -> Document/String`
  - upsert `[[providers]]` `name=cc-switch-proxy`
  - `kind=openai`
  - `base_url={proxy}/reasonix/v1`（传入值已含或函数内拼接，与 Kimi 调用约定一致并写清）
  - `models=["cc-switch-proxy-default"]`，`default` 同名
  - `api_key_env=CC_SWITCH_PROXY_API_KEY`，`no_proxy=true`
  - **不设** `models_url` / `chat_url`
  - 设置 `default_model`（推荐 `"cc-switch-proxy"`）
- [ ] `detect_proxy_takeover` / `is_proxy_takeover_active`
- [ ] `cleanup_proxy_takeover`（只删占位 provider，恢复 default_model 策略与 Kimi/Grok 对齐）
- [ ] `.env`：读写 `CC_SWITCH_PROXY_API_KEY=PROXY_MANAGED`；关闭时还原或删除
- [ ] 幂等、端口变更替换、保留其它 providers/plugins/未知键测试

重点文件：

- `src-tauri/src/reasonix_config.rs`
- 对应单元测试（模块内或 `src-tauri/tests/`）

完成门禁：转换在内存完成；失败不写用户文件。

## 4. 阶段 D：ProxyService 接管 / 恢复 / 热切换

- [ ] `ProxyTakeoverStatus` 后端增加 `reasonix`
- [ ] backup / apply takeover / restore backup / restore SSOT / cleanup
- [ ] stop protection、crash recovery、退出恢复循环纳入 Reasonix
- [ ] `set_takeover_for_app(Reasonix)` 接入共用启停编排
- [ ] `ProviderService::switch`：接管中走 per-app lock + 热切换；Live 保持代理占位；备份更新真实 default
- [ ] 检测：enabled + 占位 URL 匹配当前端口 + 备份存在

重点文件：

- `src-tauri/src/services/proxy.rs`
- `src-tauri/src/services/provider/mod.rs`
- `src-tauri/src/commands/proxy.rs`
- `src-tauri/src/proxy/types.rs`

参考调用（Kimi）：

- `kimi_config::apply_proxy_takeover(&format!("{proxy_url}/kimicode/v1"), ...)`
- Reasonix 对应：`.../reasonix/v1`

完成门禁：停止/崩溃不留下指向已停代理的 live URL。

## 5. 阶段 E：代理路由与模型映射

- [ ] `ProxyServer::build_router` 注册：
  - `POST /reasonix/v1/chat/completions`
  - `GET /reasonix/v1/models`
- [ ] handler 固定 `app_type=reasonix`（独立队列 / 熔断 / 用量）
- [ ] `apply_reasonix_upstream_model`：占位 `cc-switch-proxy-default` → attempt `default`/`models[0]`；空则 fail-closed
- [ ] 按 attempt `kind`：
  - `openai` → Chat 上游
  - `anthropic` → Chat 入站转 Messages 出站（复用现有转换器）
- [ ] forwarder 在 reasonix 分支调用 remap（对齐 `apply_kimi_upstream_model` 接线处）
- [ ] 单测：remap / 混合 P1 openai+P2 anthropic（可 mock）

重点文件：

- `src-tauri/src/proxy/server.rs`
- `src-tauri/src/proxy/handlers.rs`
- `src-tauri/src/proxy/forwarder.rs`
- `src-tauri/src/proxy/providers/codex.rs`（或新建 `reasonix.rs` 放 remap，并在 `providers/mod.rs` 导出）

完成门禁：占位符不会打到上游；归因不会落到 Codex/Kimi 路径。

## 6. 阶段 F：前端与 i18n

- [ ] 接管开关 / 状态展示接 `reasonix`
- [ ] 设置页故障转移：Reasonix 队列编辑
- [ ] 用量筛选含 `reasonix`
- [ ] `en` / `zh` / `zh-TW` / `ja` 文案

重点文件：

- `src/lib/api/types.ts`、相关 proxy API
- 设置页故障转移组件、用量 filter
- `src/i18n/locales/*.json`

完成门禁：`pnpm typecheck` 通过。

## 7. 阶段 G：验收与回归

- [ ] design §14 / PRD §5 清单全部勾选
- [ ] `cargo test`（reasonix_config + proxy + database migration）
- [ ] `cargo check --lib`
- [ ] `pnpm typecheck`
- [ ] 手动：Reasonix CLI 接管一条请求；关闭接管恢复；failover 两条队列
- [ ] 确认 Claude/Codex/Kimi/Grok 现有接管无回归

## 8. 明确非目标（实现时跳过）

- Reasonix OAuth
- Responses 入站
- 修改上游 `ccswitch.go` 的 `enabled_codex` → `enabled_reasonix`
- OpenCode / OpenClaw 代理
- 完整 live→DB 导入（SSOT 以 DB + 备份为准即可；可留 TODO）

## 9. 关键契约速查（防踩坑）

```text
Live base_url  = http://127.0.0.1:<port>/reasonix/v1
CLI 实际请求  = POST {base_url}/chat/completions
路由注册      = POST /reasonix/v1/chat/completions

占位 provider = cc-switch-proxy
占位 model    = cc-switch-proxy-default   # 禁止裸名 default
.env          = CC_SWITCH_PROXY_API_KEY=PROXY_MANAGED
kind          = openai（Live 固定）
no_proxy      = true（custom 全局代理模式下可能无效，需提示）
```

## 10. 文档索引

| 文档 | 用途 |
|------|------|
| `docs/plans/reasonix-proxy-parity-prd.md` | 产品目标、验收、风险 |
| `docs/plans/reasonix-proxy-parity-design.md` | 详细技术设计与源码证据 |
| `docs/plans/reasonix-proxy-parity-implement.md` | 本执行清单 |
| `docs/plans/kimi-parity-design.md` | 结构与闭环对照（Responses≠Chat） |
