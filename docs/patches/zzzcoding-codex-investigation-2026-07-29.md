# zzzcoding Codex-only 渠道调查（未接入，已放弃）

**Date:** 2026-07-29
**Status:** Aborted — 上游全站宕机 + 用户决定放弃该 key。VPS / NewAPI / Kimi 配置**零改动**

## 调查对象

- 上游：`https://api.zzzcoding.org`（用户提供的 `sk-d71f…97d6`，key 已明文出现于会话，建议持有方轮换）
- 目标：接入 ZG NewAPI 并给 Kimi Code CLI 配别名

## 实测结论（直连上游，本机 + VPS 双側）

| 探测 | 结果 |
|---|---|
| `GET /v1/models` | 200，20 个模型（gpt-5.2/5.4/5.4-mini/5.5/5.6/5.6-luna/sol/terra、gpt-5.3-codex-spark、codex-auto-review、gpt-image-1/1.5/2、gpt-4o audio/realtime） |
| `POST /v1/chat/completions` | **403 `This account only allows Codex official clients`**（账号级锁定，非限流） |
| `POST /v1/responses` 无 Codex 头 | 403 同上 |
| `POST /v1/responses` + Codex 指纹头（`originator: codex_cli_rs`、`User-Agent: codex_cli_rs/*`、`session_id`、`stream:true`、`Accept: text/event-stream`） | 越过客户端校验，进入 **429 并发限流**层（`Concurrency limit exceeded` / `Too many pending requests`） |
| 约 40 分钟后全站复测 | `api.zzzcoding.org` 源站 `8.216.48.195:443/80` **connection refused**（VPS 直连实测，排除本机代理因素）；apex/www 无 DNS。服务整体下线 |

判读：该 key 属 **Codex CLI 限定账号**，唯一通路是 Responses 协议 + Codex 指纹头，且账号并发极紧（疑似单并发）。随后服务全站宕机，用户决定放弃。

## 本次未做任何配置变更

- VPS：仅做了一次 DB 备份（`one-api.before-zzzcoding-20260729-173814.db`），放弃后已删除；channels/abilities/options 未触碰，容器未重启
- 本机：`~/.kimi-code/config.toml` 未修改；临时 SSH 助手脚本已删除；key 未写入任何项目文件（全仓扫描确认）

## 可复用路径：Codex 锁客户端上游接入 NewAPI 的正解

本次调查确认的接入方案，对后续同类「only allows Codex official clients」上游直接适用：

1. **渠道模板 = channel 24 `welfare-0xpsyche-responses`**（已在线运行）：
   - `type=1`(OpenAI)，`base_url` 不带 `/v1`（防 NewAPI 自动补路径成双 `/v1`）
   - `header_override = {"User-Agent":"codex-cli/0.101.0"}`
   - 独立别名 + `model_mapping`（如 `welfare-codex-gpt-5.6-sol → gpt-5.6-sol`），opt-in 不混入主力权重池
2. **Kimi Code CLI 侧**：provider `type = "openai_responses"`（官方文档合法类型，本机 0.29.1 已有 `zg-newapi-responses` 先例），模型别名指向 NewAPI 上的映射名
3. **兜底协议转换**：若 Responses 直转仍 403，启用预留的 `global.chat_completions_to_responses_policy`（chat→responses 转换，把新渠道 id 填入 `channel_ids`），改 options 后**必须 `podman restart new-api`**（仅启动加载）；Kimi 侧改用普通 `openai` provider 走 `/v1/chat/completions`
4. **本版本 NewAPI 管理面 `POST /api/channel` 会 Go panic**：新渠道走 SQLite 直插 + `podman restart`；`channel_info` 必须用 `sqlite3.Binary` 写 BLOB JSON（`{"is_multi_key":false,…}`），abilities 行同步 priority/weight
5. **并发限流纪律**：此类账号全部串行单次测试，429 间隔 ≥30s，最多 3 次重试即停

## Related

- 预留策略说明：`docs/ops/newapi-vps-minimal-state-2026-07-28.md`（「遗留惰性配置」节）
- 转换策略细节：`docs/plans/newapi-adaptive-routing-2026-07-27.md`（chat→responses 唯一通路一节）
- welfare 先例渠道：`docs/ops/newapi-kimi-mcp-claude-current-state-2026-07-28.md`
