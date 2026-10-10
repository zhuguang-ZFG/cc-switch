# any/agent 渠道 Claude+GPT 四路修复（2026-10-10）

**Status:** 已生效（codex 实弹归因 ch126/127、管理测试 4/4、冒烟门禁 ALL OK）
**Scope:** NewAPI ch72/86/126/127/134/135/136 + abilities、`~/.codex/config.toml` 接线重放。未触碰 cc-switch 本体、桥进程、OMP 配置。
**触发:** 用户指令"修复 CCS 中的 claude 和 gpt 的 any 和 agent 渠道"；期间用户实报 codex `401 Invalid token`。

## 1. 根因（四路各异）

| 渠道 | 根因 | 定性 |
|---|---|---|
| ch72 any-Claude | `type` 漂移 14→1（chat 面）：8789 桥对 Claude 模型只服务 `/v1/messages`（`proxy.cjs` 明示 "bridge only serves non-Claude models via the responses surface"），type-1 形态下管理探测与真实流量恒 400——**上游恢复也接不进来**；`test_model` 同时被清空（违反 `ANYROUTER_TEST_MODEL` 契约） | 配置损坏 |
| ch86/134/135/136 agent-Claude | 10-02 起上游 Claude 预算池 402 停泊；今日直探 + 管理测试 **4/4 全 200 = 池已回血**，只差一步启用 | 上游已愈，待启用 |
| ch126 any-GPT | 10-04 手动停泊（当时 anyrouter 的 gpt-6-astra Azure 部署被摘）；今日 `codex exec` 实弹直连 200 = 上游部署已重铺（管理面合成请求仍是假阴性 404，不作数） | 上游已愈，待启用 |
| ch127 agent-GPT | 在池服务 deepseek-v4-flash，但 `tag=agentrouter-window`、`auto_ban=1` 与 `codex_window_pool` 豁免契约（`codex-resource-window`+`auto_ban=0`）不符 → 下个 0/8/16 预算窗 Guardian 会误隔离整渠道 | 豁免契约漂移 |
| Codex 接线 | cc-switch 覆写漂移（预言过 §4 风险）：`model_provider` 回到断链 custom、`[model_providers.any]` 整块消失；且 secrets `newapi_probe_key` 对应 token 已被禁用（tokens 表 id=6 status=3）→ 重放旧值直接 **401 Invalid token** | 投影覆写 + token 失效 |

**本 fork token 存储事实**（排查 401 时实证）：`tokens.key` 为明文去 `sk-` 前缀存储（48 字符），可由 DB 值重组 `sk-<key>` 作客户端凭据；旧 probe key 的存储形态匹配 id=6（status=3 禁用），证明失效是 token 被禁而非链路问题。

## 2. 变更

DB 直写（`PUT /api/channel/` 本 fork 对最小体拒收，双写契约沿用）；备份 `new-api-before-any-agent-restore-20261010-104604.db`：

1. **ch72**：`type=1→14`、`test_model='claude-opus-5'`。上游当日仍 429 负载窗（8789 桥 3/3 复测），**维持 status=2 fail-closed**——形态修复保证窗口结束即可一步启用/自动恢复。
2. **ch86/134/135/136**：`status=1` + abilities `enabled=1`（5 模型行×4 渠道，default 组）；另按 10-10 批量镜像配方补 **Free 组 21 行**（4 条 Claude 腿×5 模型 + ch126×1）。
3. **ch126**：`status=1` + abilities `enabled=1`（gpt-6-astra，default+Free），`other_info` 记复活归因。
4. **ch127**：`tag='codex-resource-window'`、`auto_ban=1→0`，恢复窗口豁免。
5. **`~/.codex/config.toml`**：重放 any 接线（`model_provider="any"`、`model="gpt-6-astra"`、`[model_providers.any]` base `http://127.0.0.1:3002/v1` + wire responses）；经管理 API 新建客户端 token `codex-any-agent`（id=10，default 组 unlimited）填入 `experimental_bearer_token`。备份 `config.toml.bak-20261010-104851-any-restore`。踩坑：`re.sub(count=1)` 全局替换会先命中 `[model_providers.custom]` 的同名 bearer 行——**必须限定 block 内替换**；custom 块 bearer 已从备份还原。

## 3. 验证

| 检查 | 结果 |
|---|---|
| 管理测试 ch86/134/135/136 `claude-opus-5` | 4/4 通过（10:47，quota=20 各） |
| codex 直连 anyrouter.top `gpt-6-astra`（实弹） | `ANYPROBE_OK`，11.5k tok |
| codex 默认链端到端（无任何覆盖） | `CUTOVER_OK`/`AGAIN_OK`；日志归因 `use_channel:['126']` 直接服务计费 540684 quota ✓，`['127','182']` 轮转 ✓ |
| agentrouter `/v1/responses` 直探 | gpt-6-astra 2/2 key 全 200；gpt-5.6-sol 503「当前分组 default 下无可用渠道」（上游撤腿，非 402） |
| `newapi-local-smoke.py` | **ALL OK**：`pool capacity claude-opus-5 enabled=7 ids=[3,9,18,86,134,135,136]`、`unexpected_disabled=none`、72 在 accepted_disabled |

## 4. 剩余卡点（上游侧，本地无解）

- **any-Claude（ch72）**：上游全天 429（load-cap），保持停泊；窗口开后一步启用（形态已修好）。
- **agent-Claude 预算池**：修复期间实测 200↔402 摆动（池被共享消耗），网关 claude 面间歇 402 透传；上游主池 ch3/9 另有 403 余额问题。等 0/8/16 投放或 agentrouter 面板加池。
- **gpt-5.6-sol**：agentrouter 上游已撤该腿（503 无可用渠道，非预算耗尽），路由保留待补货。
- **cc-switch 覆写风险**：`config.toml` 是 codex provider 投影，下次切 provider 会再抹掉 any 块——恢复=重放本文 §2.5。

## 5. 回滚

```text
~/.new-api-local/backups/new-api-before-any-agent-restore-20261010-104604.db   # 渠道/abilities 整库
~/.codex/config.toml.bak-20261010-104851-any-restore                           # Codex 接线
新 token 摘除：PUT /api/token/ status=2（codex-any-agent, id=10）
```
