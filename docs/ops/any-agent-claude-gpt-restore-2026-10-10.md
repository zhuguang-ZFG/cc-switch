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

## 5. 追加：BBcloud 转售面 403 假死（11:0x，用户实报）

- 症状：codex 连续 `403 Astra由BBcloud提供 友情提醒：请求未能完成`（3002 `/v1/responses`）。
- 归因：**ch126 anyrouter 上游**（anyrouter.top 的 astra 由 BBcloud 转售，文案为转售方临时拒答透传），非本地接线错误；403 不在 `AutomaticRetryStatusCodes` 且 ch126 未映射 403 → 不换渠道重试，叠加 `channel_affinity`（TTL 300s）把会话钉死在 ch126，形成"继续/继续/同一 403"。
- 修复：ch126 `status_code_mapping` 补 `"403":"503"`（ch128 SharedChat 先例），让 403 参与一次跨渠道重试，兄弟腿 ch127(p40)/ch182(p20)/ch193/194(p10) 兜底。
- 验证：映射生效后 codex 实弹 3/3 成功（11:03:05/18/27，均归因 ch126，上游此刻自愈；403 真实形态的 failover 以映射语义+兄弟腿在池为准）。
- 边界：ch126 `auto_ban=0`，403 不会自动打黑；若上游演化为持续 403（key 级），错误扫描关键词归因后再人工处置。
- **注（11:1x 用户裁决后追记）**：本节"兄弟腿兜底"思路已被 §7 硬隔离取代——403→503 映射保留，但 any 流量的 failover 域只有 ch126 自身。

## 6. 回滚

```text
~/.new-api-local/backups/new-api-before-any-agent-restore-20261010-104604.db   # 渠道/abilities 整库
~/.codex/config.toml.bak-20261010-104851-any-restore                           # Codex 接线
新 token 摘除：PUT /api/token/ status=2（codex-any-agent id=10 / codex-agent id=11）
```

## 7. 追加：any/agent 流量硬隔离（11:1x，用户裁决）

**用户裁定：any 流量只准走 any 渠道、agent 流量只准走 agent 渠道**——§5 的
跨池兜底设计（any→ch127/182/193/194 兄弟腿）就此作废；BBcloud 403 的正确
处置是隔离而非借道。

实现（NewAPI 分组隔离，`default`/`Free` 池原样保留给 OMP 等既有消费者）：

| 组 | 成员渠道 | 模型面 |
|---|---|---|
| `any` | ch126（GPT 活腿）、ch72（Claude，enabled=0 停泊随行） | gpt-6-astra；claude 全家（待上游 429 窗结束一步启用） |
| `agent` | ch127（GPT）、ch86/134/135/136（Claude） | gpt-6-astra/gpt-5.6-sol/deepseek-v4-flash；claude 全家 |

- abilities 按 default 行镜像插入 29 行；`channels.group` 同步追加
  `default,any` / `default,agent`（防渠道编辑触发的 abilities 重同步抹行）。
- token 绑定：`codex-any-agent`(id=10) group `default→any`；新建
  `codex-agent`(id=11) group `agent`（key 存
  `~/.new-api-local/codex-agent-token.txt` 0600，不入仓；sk- 形态同
  §1 存储事实）。
- **分组权限门（本轮新契约）**：token 组请求 403 `无权访问 %s 分组` 的根因
  是 **`UserUsableGroups` 选项**（service.GroupInUserUsableGroups），
  `GroupRatio` 单独注册不充分——两个选项都经 `PUT /api/option/` 登记了
  any/agent。DB 直写 options 表不会热生效，必须走 option API（广播刷新内存）。
- `~/.codex/config.toml`：`[model_providers.any]` 更名 "Any GPT"（保持默认
  provider），新增 `[model_providers.agent]` "Agent GPT"（同 3002 base，
  agent token）；备份 `config.toml.bak-20261010-111011-any-agent-separate`。
  切换用法：`codex exec -c model_provider="agent" …`。

验证（11:1x，全部隔离正确）：

| 探针 | 结果 | 归因 |
|---|---|---|
| codex 实弹 默认(any) | 成功 ×2 | `use_channel:['126']`，零跨池 ✓ |
| codex 实弹 `-c model_provider=agent` | 503 `Budget pool quota has been exhausted` | agentrouter 预算池签名（ch127 专属错误上冒，未借道）✓；池回血前 agent-GPT 暂不可用 |
| any token × gpt-5.6-sol | 503 `No available channel … under group any (distributor)` | 隔离反证 ✓ |
| agent token × gpt-5.6-sol | 上游签名 `当前分组 default 下…无可用渠道` | 打到 agentrouter（其自有分组文案，§8.4 教训同族）✓ |
| agent token × claude-opus-5 | 402 预算池耗尽 | agent Claude 池未回血（§4 观察项延续），归因正确 ✓ |
| any token × claude-opus-5 | 503 under group any | ch72 enabled=0 随行隔离 ✓ |
| `newapi-local-smoke.py` | **ALL OK** | 零新增违规 |

边界与遗留：

- **agent 侧上游此刻普遍缺货**（GPT 402、sol 503、Claude 402）——隔离让
  "agent 不可用"如实呈现，不再被 any/羊毛腿假成功掩盖；上游回血后无需本地
  变更（0/8/16 窗先例）。
- chat 面合成探针打 any×astra 得 404 = anyrouter codex 门假阴性（§3 既有
  教训），隔离结论以 codex 实弹 + distributor 错误报文为准。
- ch86/134/135/136 无 402→503 映射，agent-Claude 面 402 直接透传（池内
  四腿同池轮询，语义正确，未动）。
- 回滚：删 any/agent 两组 abilities 行 + `channels.group` 还原 + token 10
  group 改回 default + option 两项还原；备份
  `new-api-before-any-agent-separate-20261010-110936.db`。

## 8. 追加：cc-switch codex 路由接管态与 provider 重接线（11:3x–11:5x，用户授权"你搞"）

- 用户报"切到 agent 还是走 any"——三重归因，前两层非故障：
  1. 上午接线把 `config.toml` 改成 `model_provider="any"` 直连 3002，**绕开了
     cc-switch 15721 代理接管**；cc-switch 路由切换（日志三次"切到 agentrouter…
     完成"均成功）对流不进 15721 的流量无感。cc-switch 11:27 重启 attach 后
     客户端配置写回 `custom→15721 (PROXY_MANAGED)`，接管恢复。
  2. AgentRouter 路由真实转发目标 8788 桥回 401 `invalid api key`：**8788 入口
     令牌 = secrets `agentrouter_proxy_key`（64 字符，fp ae204e1c）**，而
     provider 里存的是 agentrouter 上游池 key sk-vBV8P…（51 字符，fp b433b22b）
     ——上游 key 当本地门票用，桥入口即拒，从未出网。
  3. 用户在 UI 编辑 provider 未保存落库（DB 复核 base_url/key 原样）。
- 处置（用户授权直接改数据行；未动 schema、未动应用进程）：
  `providers.agentrouter-1790248366298.settings_config` 重接线到 §7 的 agent 组——
  `base_url http://127.0.0.1:8788 → http://127.0.0.1:3002`（**不带 /v1**：cc-switch
  forwarder 自动拼 `/v1/responses`），`OPENAI_API_KEY → codex-agent token（sk-，
  组 agent）`；原值备份 `~/.cc-switch/backups/codex-agentrouter-provider-before-3002-20261010-114959.json`。
  用户切换路由+重启 cc-switch 生效。
- 验证（11:52）：codex 实弹 → cc-switch 日志 `请求目标: http://127.0.0.1:3002/v1/responses`
  ×3，响应体为 agentrouter 预算池 402→503 签名（ch127 专属）——接线成立；
  agent-GPT 实际可用性等上游 16:00 投放窗。
- 最终形态：**Any 路由 → 8789 桥（anyrouter 直连，无 3002 记账）；AgentRouter
  路由 → 3002 agent 组（ch127 + Claude 池，有记账归因）**。Any 侧是否同样
  接 3002 any 组（换 token10 + base `http://127.0.0.1:3002`）待用户裁决，
  当前 8789 直连功能正常、隔离方向正确。
- **11:5x 用户裁决"同意"，Any 路由同法接入 3002 any 组**：
  `providers.any-codex-1788960193007` base `8789→http://127.0.0.1:3002`、key
  → token10（`codex-any-agent`，组 any）；备份
  `codex-any-provider-before-3002-20261010-115429.json`。**forwarder 按请求现读
  provider 配置，无需重启即生效**。验证：实弹 15721→`请求目标: 3002/v1/responses`
  → 计费日志 `ch=126 use_channel:['126']` ×2 ✓。至此两条 cc-switch 路由统一
  经 3002 分组：Any→any 组（仅 ch126/72）、AgentRouter→agent 组（仅
  ch127/86/134/135/136），双向有 NewAPI 记账与归因。
- 契约要点（新增）：
  - cc-switch **代理接管态**下，直改 `config.toml` 的 `model_provider` 会让
    UI 路由切换整体失明——两者只能选一条控制路径；
  - cc-switch provider 的 `base_url` 写值**不带你 /v1**，forwarder 拼接路径；
  - 8788/8789 桥的入口令牌与上游池 key 是两个域，provider `OPENAI_API_KEY`
    必须填**入口令牌**（桥的 `--api-key`/env），填上游池 key 会在桥入口 401；
  - cc-switch provider 编辑若 UI 保存不生效，改 DB 数据行 + 切路由/重启可生效，
    必须先备份原 `settings_config`。

## 9. 追加：OMP deepseek-v4-flash 杂错根治——ch127 移出 default/Free 链（12:0x）

- 用户报"OMP DeepSeek 几乎不可用，各种错误码"；OMP 走 `zg-newapi` provider
  **直连 3002**（models.yml baseUrl），token 组 default。
- OMP 日志错误普查（当日 warn/error，按 provider 归因到网关报文）：
  | 错误形态 | 渠道归因 | 定性 |
  |---|---|---|
  | 401 `unauthorized client detected … discord.gg/…` | ch127 | agentrouter deepseek 上游客户端指纹门（透传） |
  | 400 ``content[].thinking … must be passed back`` ×2 | ch127 | 上游 anthropic 风格校验；OMP 实际已回传 `reasoning_content`（openai 形态），是其上游转换层假拒绝 |
  | 400 `Invalid schema for function 'wait'` | ch127 | 上游严格 schema 校验透传 |
  | 401/400 包装形态 `openai_error bad_response_status_code` | ch127 | 同族签名（网关二次包装） |
  | 502 `bad response status code` | ch118 seeseed | 00:50 时链头还是 118（旧链，50550d66 已降级） |
- **归因方法**：OMP 的 `~/.omp/logs/http-400-requests/*.json` 存了 400 原始
  请求（含 headers 里的 token——**只读结构、绝不打印**）；用 body 按 request_id
  grep 网关日志即得渠道级归因。完整原样重放经 ch180 **通过**（228KB、61 条
  消息含 reasoning_content/tool_calls，16.5s 正常计费）→ 请求形态无罪，
  病灶是 ch127 上游。
- 根因：凌晨错误发生在 50550d66（链头 118→180）之前；修链后当日无新 deepseek
  provider error。但 **ch127 仍在 default/Free 链内当 p40 备胎**——ch180 一旦
  抖动重试落到 ch127，就会出现这批 400/401"乱码级"不可重试错误（400 不触发
  换渠道重试），外加 11:52 实测预算池 503 风暴。codex-window 语义上 ch127 的
  deepseek 腿本就该只服务 agent 组。
- 修复（最小变更，备份 `new-api-before-deepseek-chain-clean-20261010-120810.db`）：
  `abilities` 直写 **ch127×deepseek-v4-flash 的 default+Free 行 enabled=0**
  （agent 行保留给 codex agent 组）。链收敛为 default: 180(p51)→118(p25)；
  Free: 118 独腿。~60s 缓存同步生效。
- 验证：变更后 3/3 探针 200（~2.6s），归因 `use_channel:['180']` 零 ch127
  尝试；full-replay 计费行 `ch=180 quota=25737` ✓；`newapi-local-smoke.py`
  **ALL OK**（含 critical ability posture）。
- 回滚：`UPDATE abilities SET enabled=1 WHERE channel_id=127 AND
  model='deepseek-v4-flash' AND group IN ('default','Free')` 或整库还原备份。
