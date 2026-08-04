# Cursor BYOK → NewAPI 高级模型配置（2026-08-04）

**状态:** 已生效，Cursor 3.12.17 Agent 模式实测返回模型回复（`CURSOR_NEWAPI_OK3`，模型 `zg-deepseek-v4-pro`）。

## 最终配置

| 项 | 值 |
|----|-----|
| Override OpenAI Base URL | `https://aliyun.donglicao.com/v1`（**必须公网 HTTPS**） |
| OpenAI API Key | OMP `~/.omp/agent/models.yml` → `providers.zg-newapi.apiKey`（51 位 `sk-`，与 cc-switch `zg-gateway-claude` 的 token 不同） |
| 模型 | `zg-claude-opus-5` / `zg-gpt-5.6-sol` / `zg-glm-5.2` / `zg-grok-4.5` / `zg-deepseek-v4-pro`（NewAPI 自定义别名，聊天中选择器里显式选中，勿用 Auto/官方模型） |

落盘：`%APPDATA%\Cursor\User\globalStorage\state.vscdb` → `openAIBaseUrl` / `useOpenAIKey=true` / `availableDefaultModels2`（`defaultOn=true` + `namedModelSectionIndex`）/ `aiSettings.userAddedModels` / `modelOverrideEnabled`；key 经 Electron safeStorage（v10 AES-256-GCM + DPAPI）加密存 `secret://cursorAuth/openAIKey`。

## NewAPI 侧（VPS `47.112.162.80`）

2026-07-28 后旧 `zg-*` 别名全部失效（503 `No available channel`，7-29 审计重构渠道时未保留）。本次重建：

- 备份：`/opt/new-api/data/backups/one-api.before-cursor-models-20260804-233732.db`
- 对每个别名：从源模型复制 abilities（enabled/priority/weight/tag），`channels.models` 追加别名，`model_mapping[alias]=源模型`，`options` 定价（ModelRatio/CompletionRatio/CacheRatio/CreateCacheRatio）同步源模型值
- 映射：`zg-claude-opus-5→claude-opus-5`、`zg-gpt-5.6-sol→gpt-5.6-sol`、`zg-glm-5.2→glm-5.2`、`zg-grok-4.5→grok-4.5`、`zg-deepseek-v4-pro→deepseek-official-v4-pro`
- 改完 `podman restart new-api`；`integrity_check ok`

## 两个关键坑（社区验证，勿再试错）

1. **`This model does not support custom API keys`** — 选中的是 Cursor 官方目录模型（或 `Auto`），BYOK 只走自定义模型。发送前在模型选择器显式选 `zg-*`。
2. **`Access to private networks is forbidden`** — Cursor 将 BYOK 请求**全部经 Cursor 云端后端转发**，云端访问不了内网/loopback。OMP 用的 Tailscale 内网 `http://100.103.82.78:3000/v1` 在 Cursor 里必被拒；必须公网域名。参考：[mattmireles/local-motion cursor-private-network-block-guide](https://github.com/mattmireles/local-motion/blob/main/README/Guides/cursor-private-network-block-guide.md)、[cursor-custom-model-validation-guide](https://github.com/mattmireles/local-motion/blob/main/README/Guides/cursor-custom-model-validation-guide.md)。

## 备份

- Cursor：`state.vscdb.bak-newapi-models-20260804154222`
- NewAPI：见上

## 验证

- `/v1/models`（公网 + OMP key）200，34 模型
- Cursor UI 实测：新聊天 → 选中 `zg-deepseek-v4-pro` → 「Reply with exactly: CURSOR_NEWAPI_OK3」→ 模型原样回复 `CURSOR_NEWAPI_OK3`（Agent 模式）
- 其余别名经网关直测：`zg-gpt-5.6-sol` / `zg-glm-5.2` / `zg-grok-4.5` chat 200；`zg-claude-opus-5` 首字慢（≥120s 超时），属 Opus 主池波动，非配置问题

## 追加（2026-08-05）：K3 / 官方 Flash / Hy3 / atom Flash

第二批复用同一流程，新增 4 个模型，Cursor 现共 9 个 `zg-*`：

| 模型 | 源 | 备注 |
|------|----|----|
| `zg-k3` | `k3`（ch33 kimi-official-k3） | Kimi K3，1M ctx |
| `zg-deepseek-official-v4-flash` | `deepseek-official-v4-flash`（ch42 deepseek-official） | 官方 DeepSeek V4 Flash |
| `zg-hy3-preview-agent` | `hy3-preview-agent`（ch44 codebuddy→WorkBuddy） | Hunyuan Hy3，196k ctx |
| `zg-deepseek-v4-flash` | `deepseek-v4-flash`（ch53 atomcode-bridge） | DeepSeek V4 Flash（atomcode） |

备份：`one-api.before-cursor-models2-20260805-002853.db`、`one-api.before-cursor-models3-20260805-004350.db`；Cursor `state.vscdb.bak-newapi-models2-*` / `bak-newapi-models3-*`。

**WorkBuddy 的 `gpt-5.6-sol` 未接入**：WorkBuddy 上游（OMP 本地代理 8787）实测 403（8-03 已记录同款硬 403），NewAPI ch44 也未承载。同模型聚合池 `zg-gpt-5.6-sol` 已在 Cursor 可用。

### atom 代理修复（ch53 断连根因）

- atomcode 代理 `proxy.js` 加固后默认只监听 `127.0.0.1`（防公网中继），NewAPI ch53 经 Tailscale `100.83.32.95:9457` 连不上 → 渠道自动封禁（status=3）。
- 修复：代理以 `HOST=100.83.32.95` 重启（仅 Tailnet 私密接口）；ch53 `key` 从占位 `dummy` 改为代理 `LOCAL_API_KEY`（代理接受 `Authorization: Bearer`）；恢复 status=1 + abilities。
- ch44（WorkBuddy）同理走 Tailscale 8787，渠道一直正常。

## 验证（追加）

- Cursor UI 实测：`zg-hy3-preview-agent` → `HY3_OK`；`zg-deepseek-v4-flash` → `ATOM_FLASH_OK`；`zg-k3` 用户会话实测 Worked。
