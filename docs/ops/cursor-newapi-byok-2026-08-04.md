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

## 追加（2026-08-05）：agentrouter / WorkBuddy sol 别名

Cursor 现共 13 个 `zg-*` 模型，新增：

| 模型 | 源 | 状态 |
|------|----|----|
| `zg-agent-claude-opus-5` | ch45 agentrouter | 配置就位；**上游待恢复**（见下） |
| `zg-agent-claude-opus-4-8` | ch45 agentrouter | 同上 |
| `zg-agent-gpt-5.6-sol` | ch45 agentrouter | 同上 |
| `zg-wb-gpt-5.6-sol` | ch44 WorkBuddy | 配置就位；**上游 403**（见下） |

NewAPI 备份：`one-api.before-cursor-models4-20260805-013232.db`；Cursor `state.vscdb.bak-newapi-models4-*`。

### converter.py 误删恢复（2026-08-05）

`~/.kimi-code/` 整目录被误清空（converter、agentrouter-proxy、config.toml、mcp.json 全丢）。已恢复：

- `converter.py`：从 `github.com/HanHan666666/codebuddy2openai` 拉原版（`converter.py.orig` 留档），按 `docs/patches/workbuddy-gpt-sol-converter-keypool-2026-07-31.md` 规格重建修改版：custom 模型路由（`~/.workbuddy/models.json`）、key 池冷却（`custom_keys.json`，`CUSTOM_KEY_COOLDOWN_S=180`，req_epoch 防竞态）、WorkBuddy Electron UA/Referer/Origin 头（`WB_UA`/`WB_REFERER`/`WB_ORIGIN` 环境变量可覆盖）、错误分类（仅 502/503/504/unavailable 重试+冷却）、日志脱敏、URL 校验、`/health` 脱敏+鉴权、`/v1/models` 合并 custom。
- 监听 `100.83.32.95:8787`（Tailscale 私密接口，供 NewAPI ch44），经 NewAPI 验证 `zg-hy3-preview-agent` 200 恢复。
- `custom_keys.json`：用 `models.json` 的 key#4 重建（单 key；原 4-key 池其余 key 随被删文件丢失）。

### 两个上游阻塞（需用户侧解决）

1. **WorkBuddy `gpt-5.6-sol` 403**（`unsupported_client`）：freemodel.dev 按请求头校验 WorkBuddy 客户端。converter 改造已就位，但 WB_UA/Referer/Origin 真实值随被删修改版丢失；猜测组合均 403。恢复途径：重启 WorkBuddy 带 `--remote-debugging-port` 抓真实请求头，或用户提供。当前 `zg-wb-gpt-5.6-sol` 经网关 403。
2. **agentrouter 上游 key 丢失**：`agentrouter-proxy.py`（含 4 个上游 key）随 `.kimi-code` 删除，GitHub 无来源；`secrets.json`/`models.yml`/daemon spec 中仅剩本地鉴权 key（三者相同，非上游 key）。恢复途径：agentrouter.org 控制台重新生成 key 后重建代理（逻辑简单：转发 `agentrouter.org`/`ps.air-outer.com` + `claude-cli/1.0.0 (external, cli)` UA + key 池）。当前 `zg-agent-*` 经网关超时（8788 未监听）。

## 追加（2026-08-05 深夜）：OMP 本地代理全量恢复

用户提供 agentrouter.org 上游 keys（3 个有效，去重自 4 个）+ 备用域名 `ps.air-outer.com`（大陆直连可用，无需 7897 代理；`agentrouter.org` 需经 `127.0.0.1:7897`）。

- **重建 `agentrouter-proxy.py`**（`~/.kimi-code/proxies/agentrouter-proxy/`，keys 独立存 `keys.json`）：双上游（air-outer 直连 → agentrouter 走 env proxy）、`claude-cli/1.0.0 (external, cli)` UA、key 池轮询+冷却、本地鉴权（`05Otq…`，ch45 渠道 key 已从占位 `any` 修正为同值）。验证：`claude-opus-5`/`claude-opus-4-8`/`gpt-5.6-sol` 经网关 200。
- **三个本地代理统一监听 `0.0.0.0`**（converter 8787 / agentrouter 8788 / atomcode 9457）：OMP 走 `127.0.0.1`、NewAPI ch44/45/53 走 Tailscale `100.83.32.95`，双路径都要通；均有鉴权（converter 无 key 校验、其余带本地 key）。
  - **更正（2026-08-05 下午）**：实际生效的绑定由 `~/.omp/guardian/secrets.json` 的 `local_proxy_bind_host` 决定，当前值为 `100.83.32.95`——三个代理只绑 Tailscale 接口，`127.0.0.1` 不可达。OMP `models.yml` 中 agentrouter baseUrl 亦为 `http://100.83.32.95:8788/v1`，两个消费方都走 Tailscale IP，功能自洽；若将来要恢复 127.0.0.1 路径，把该配置改为 `0.0.0.0` 并重启代理即可。
- **OMP 配置**（备份 `models.yml.20260805-bak` / `config.yml.20260805-bak`）：codebuddy 移除死项 `gpt-5.6-sol`（WorkBuddy 403 未解）；agentrouter 新增 `gpt-5.6-sol`（262k/32k/reasoning/images）；`config.yml` vision/designer 链的 `codebuddy/gpt-5.6-sol` → `agentrouter/gpt-5.6-sol`；`codebuddy/kimi-k3` 保留（converter 透传 CodeBuddy 后端实测 200）。
- **OMP 全链路实测**：codebuddy glm-5.2/hy3/kimi-k3/deepseek-v4-flash 200；agentrouter claude-opus-5/4-8/gpt-5.6-sol 200；atomcode deepseek-v4-flash 200。

OMP 改动需重启 OMP（或 reload）生效。Cursor 侧 13 个 `zg-*` 模型维持 8-05 早前状态（agent 三个别名现已可用，`zg-wb-gpt-5.6-sol` 仍 403）。

## 追加（2026-08-12）：公网出口恢复 + 模型集切换

**背景:** VPS 极简化后 new-api 容器已移除，nginx `aliyun.donglicao.com` → VPS `127.0.0.1:3000` 无监听 → 502；NewAPI SSOT 在本机 `127.0.0.1:3002`。Cursor BYOK 经云端转发、拒私网，本机地址不可直用。

**恢复:** 反向隧道 `ssh -N -R 127.0.0.1:3000:127.0.0.1:3002 lima` 由自愈循环脚本 `C:\Users\zhugu\.cursor-byok-router\newapi-edge-tunnel.vbs`（隐藏 wscript，断线 5s 重连）承载；**开机自启** = 启动目录快捷方式 `newapi-edge-tunnel.lnk`（`schtasks /Create` 本机拒绝访问，改用 Startup 文件夹）。VPS nginx 配置未动。

**Cursor 配置:** Override=`https://aliyun.donglicao.com/v1`；key=OMP `providers.zg-newapi.apiKey`；自定义模型切为 8 个 `zg-*`：`zg-k3`、`zg-deepseek-official-v4-flash`、`zg-claude-opus-5`、`zg-gpt-5.6-sol`、`zg-hy3-preview-agent`、`zg-agent-claude-opus-5`、`zg-agent-claude-opus-4-8`、`zg-agent-gpt-5.6-sol`。当日早些的 kimi.com / OpenCode Go 直连配置与本地 15722 路由代理已废弃（router 已停）。

**坑（复证）:** ① BYOK 全经 Cursor 云端转发，loopback/内网必拒；② 与目录 serverModelName 同名的自定义模型被静默去重（如 `kimi-k3`）；③ Remove 需二次对话框确认，且模型被选中/启用时按钮 disabled；④ 新增自定义模型默认不启用，需手动开；⑤ "Use OpenAI API key" 开关在重存密钥后约 2.5s 自动激活。

**验证:** 公网 `/v1/models` 200；`zg-k3` 网关直测 200；Cursor 实测 `zg-deepseek-official-v4-flash`→`ZGDS-OK`、`zg-k3`→`ZGK3-OK`；日志 `bring_your_own_key=true`、`outcome=success`、0 错误。

**追加（2026-08-12 晚）:** Cursor 新增裸聚合模型 `deepseek-v4-flash`、`qwen3.8-max`（实测 `DSAGG-OK`/`QW-OK`；qwen 首测遇 NewAPI token 限流，稍后重测通过）。**hy3 阻塞（需用户侧）**：`copilot.tencent.com` 后端 2026-08-11 14:45 起拒绝 converter 流式请求（403 code 11140 `request illegal`；非流式 400 11101）。已排除：opencode go 的 hy3（用户禁用，go 仅限 DeepSeekV4Flash）、freemodel 4 key（hy3 全 `Insufficient balance`）、指纹/preamble/SSE Accept/stream_options/端点变体均 403。修复需抓真实 WorkBuddy 客户端流式请求（客户端带 `--remote-debugging-port` 重启抓包，或用户提供新格式）。converter 默认路径（`copilot.tencent.com/v2/chat/completions`）保持不动。

**更新（同日更晚）:** 真实 WorkBuddy 客户端同报 11140（Trace `71d20905f8d446fd803c890f19d627c3`）。非流式探测全模型 400 11101（账号/模型/头均正常），唯 `stream=true` 全拒——**服务端流式收紧（8-11 14:45 起），本地无法修复**。用户侧动作：WorkBuddy 内提交反馈附 Trace ID，等后端恢复。

**WorkBuddy 全链路弃用（2026-08-12 深夜，用户决定）：**
- NewAPI：删 ch44 codebuddy（含 2 条 abilities），重启生效；`/v1/models` 已无 hy3。
- 本地代理：converter.py 已杀、8787 已关；`proxies-supervisor.py` 移除 codebuddy 条目并停用（计划任务本已禁用）；OMP `models.yml` 删除 codebuddy provider（已备份）。
- Cursor：`zg-hy3-preview-agent` 从 `state.vscdb` 存储层移除（availableDefaultModels2 条目 + aiSettings.modelOverrideEnabled/userAddedModels/modelLastUsedAt），picker 实测无残留。
- 不受影响：`gpt-5.6-sol`/`zg-gpt-5.6-sol` 有十余条非 WorkBuddy 渠道兜底（centos/fengwind/fastaitoken/agentrouter/anyrouter/sotamodel/seekai 等）。

**清理复查（同日 code review）：**
- 补漏：OMP `config.yml`（tiny 角色→`zg-newapi/deepseek-v4-flash`、providers 权重、fallbackChains 移除 codebuddy 项）；`guardian.py` 5 处（常量/LOCAL_PROXIES/探针 key 元组/重启分支/required 检查）已清并重启验证；`secrets.json` 删除 codebuddy_api_key（已备份）；cc-switch provider `local-newapi-sol` 备注去掉 codebuddy 描述。
- 验净：NewAPI DB 无 8787/freemodel/hy3 残留（token 限额、abilities、全表扫描 0）；guardian 日志无 codebuddy；8787 端口常闭、无 converter 进程、supervisor 不复活（任务已禁用）；Cursor 全库无 zg-hy3（仅 statsig/cache 内部数据，自愈）。
- 有意保留：`model_pricing` 两行 hy3 计价（历史用量计费需要）、代理历史日志/备份文件（`.bak`）、`~/.workbuddy`/`~/.kimi-code` 沉睡文件（converter 源码，若 WorkBuddy 恢复可复用）。
## 追加（2026-08-12 深夜）：聚合模型慢根因 + 渠道优先级修正

**现象:** Cursor 里 `deepseek-v4-flash`（默认聚合模型）体感很慢：公网直测 TTFB 5.6s，其余模型（zg-k3 1.19s / zg-deepseek-official 0.68s）正常。

**根因（日志 + abilities 表定位）:** `deepseek-v4-flash` 的 ability 只有 ch48 opencode-go-flash 是 enabled 且 priority=51（唯一最高）→ **100% 流量走 opencode.ai 海外上游**（本机实测 TLS 握手 1.4s、上游响应 1.5–2s+）；ch42 deepseek 官方（api.deepseek.com，TLS 0.22s / TTFB 0.33s）永远轮不到。每次请求 = 海外链路 + 上游推理 ≈ 5–6s 首 token。

**修复:** `UPDATE abilities SET priority=49 WHERE channel_id=48 AND model='deepseek-v4-flash'`（备份 `new-api.db.bak-*-ch48-prio49`）→ 现在 ch42(50) 优先、ch48(49) 兜底（Go 恢复后自动重新参与）。重启 new-api 生效。复测：TTFB 5.6s → 0.61–2.0s；日志确认 `channel_id=42`。

**顺带发现（未处理，需用户决策）:** `qwen3.8-max`（ch31 阿里云 maas）429 非瞬时限流——日志明确 `Your token-plan 1-week quota has been exhausted. reset at 08-17 14:27:00 UTC`。8-17 前该模型不可用；要恢复需换 key/加渠道。
