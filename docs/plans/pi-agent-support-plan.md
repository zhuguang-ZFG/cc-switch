# cc-switch 支持 Pi Agent — 实现规划

## 目标

把 Pi coding agent 纳入 cc-switch 受管应用（app_type = `pi`），
与 Claude/Codex/Kimi Code/Reasonix 同级的管理体验：

- Provider 管理（增删改、热切换、代理接管、成本统计）
- Live 配置写入：`~/.pi/agent/models.json` + `~/.pi/agent/auth.json`
- 代理接管（takeover）：投影 `cc-switch-proxy` provider 到 models.json，恢复时无损还原

## Pi 配置契约（已实测确认）

- `~/.pi/agent/models.json`：`{"providers": {"<id>": {name, baseUrl, api, apiKey, compat, models[]}}`
- `~/.pi/agent/auth.json`：`{"<provider>": {"type": "api_key", "key": "..."}}`（**必须带 type，裸字符串不认**）
- 常用 compat：`supportsStore:false, supportsDeveloperRole:false, maxTokensField:"max_tokens"`
- 接管投影模型约定：`cc-switch-proxy/default` → provider `cc-switch-proxy`，base_url `http://127.0.0.1:<port>/pi/v1`

## 改动面

1. **app_type 扩展**
   - `AppType::Pi` + `as_str()="pi"` + FromStr
   - DB：`proxy_config` CHECK 加 'pi'（跟随 v16 reasonix 同款迁移）、seed 行
   - fork migration 补 pi 行（INSERT OR IGNORE）
2. **`src-tauri/src/pi_config.rs`**（参照 kimi_config.rs / reasonix_config.rs）
   - JSON 读写 models.json / auth.json（非 TOML，用 serde_json）
   - `get/set_providers`、`apply_proxy_takeover`、`clear_proxy_takeover`、`is_proxy_takeover_active`
   - 快照备份与无损恢复（同 reasonix 语义）
3. **代理转发**
   - `proxy/server.rs` 路由 `/pi/v1/*`
   - Pi 客户端协议 = openai-completions，上游转换复用 codex chat 路径
   - `proxy/providers` 注册 pi wire protocol
4. **ProviderService**
   - pi 的 current provider 切换 → 写 models.json + auth.json
   - 接管期间 CRUD → 写备份不写 live（reasonix 同款）
5. **前端**
   - app 注册（图标/名称/表单），pi 用 openai 通用表单即可
   - 代理面板 takeover 开关加 pi
6. **测试**
   - pi_config 单测（投影/恢复/接管检测）
   - 迁移测试（v16→v17 或 fork migration）
   - takeover smoke（参考 kimicode_takeover_smoke）

## 工作量估计

约等于 Reasonix 那波（#2e62a8d7..1b9d9344 五个 commit 的规模）：后端 2-3 天当量，前端 0.5 天。

## 风险

- models.json 与 models-store.json 的关系要写对（pi 同时读两个，provider 注册只写 models.json）
- auth.json 的 typed credential 格式是硬约束（本次踩过）
- pi 无官方"多 provider 切换"概念，current 语义 = 改 models.json 里 defaultProvider/defaultModel

---

## 实施状态（2026-07-23，已完成）

本规划已全部落地并超出原范围，见 CHANGELOG [Unreleased] → Added (Pi agent)：

- `AppType::Pi` + DB v17 迁移（skills.enabled_pi）+ fork 迁移（proxy_config 'pi' 行）
- `pi_config.rs`：deep-merge 无损写入、形状校验、typed auth、接管/恢复
- 代理 `/pi/v1`（chat/completions + models），camelCase baseUrl 识别
- Skills 面板（MCP 故意除外：pi 核心无 MCP）、failover UI、使用统计（session_usage_pi）
- 前端全套：表单/预设/环境检查/托盘/用量看板/四语言 i18n
- 关联产出：`proxy/role_router.rs`（`[[route:MODEL]]` 角色级模型路由标记）

## 后续候选（未做）

- 通用供应商同步到 pi（后端 `to_pi_provider`）
- Profiles/项目分组、Session 管理页、Deeplink `app=pi` 导入
- 非 openai-completions 协议的协议桥（导入已 fail-closed 拦截）
