# 君の公益 muyuan.do 聚合渠道接入（ch173）（2026-10-06）

## 结论

用户提供 `muyuan.do` 连接信息（君の公益共享站，宣称上新 `mistral-large-4-0`）。
首探（21:55）该模型上游**未定价**（400 `model_price_error`）未接入；其余 25 个
模型 exact-id 接入，新建 **ch173 `muyuan-gongyi`**（type=1，p20/w1，auto_ban=1，
test_model=`ministral-3b-latest`）。**22:13 复查：上游已上架并定价
`mistral-large-4-0` + 变体 `mistral-large-4`（实弹 200）**，脚本 `--extend`
纯增量 PUT 补入，现 **27/27 全量聚合**。
**glm-5.2 为唯一有主渠道共享的模型**（ch15 sensenova-token p50/w1 继续主役，
ch173 备份位）；其余 26 模型 ch173 为唯一在营渠道。政策门禁与 OMP 门禁全绿
（零新增违规，存量三 FAIL 与 ch173 无关）。

## 上游探测证据（只读，key 不落盘）

- `GET /v1/models` → HTTP 200，0.6s，25 模型。
- `mistral-large-4-0` 首探直调 → 400 `new_api_error/model_price_error`
  （"价格尚未由管理员配置"）→ 当时排除。**22:13 复查：上游已定价并上架
  `mistral-large-4-0` + `mistral-large-4`（/v1/models 27 项，实弹均 200
  "pong"）→ `--extend` 补入**（快照 `new-api-before-muyuan-gongyi-20261006-221422.db`）。
- `ministral-3b-latest` 实弹 ping → 200 "Pong"（12 tokens）→ 渠道可消费。
- 管理端点（`/api/user/self` 等）被 Cloudflare challenge 403；API 路径正常。

## 变更

执行脚本：`scripts/ops/add_muyuan_gongyi_channel.py`
（key 走 `MUYUAN_KEY` env，dry-run 默认，幂等 resume：渠道已存在且配置一致
则复用，漂移则拒绝并留痕）。

| 项 | 值 |
|---|---|
| 渠道 | ch173 `muyuan-gongyi`（type=1，base `https://muyuan.do` 不带 /v1） |
| 模型 | 27 个 exact-id（GLM-5.3-200k、GLM-5.2-200k、glm-5.2、glm-4.5-flash、grok-4.7、qwen3.8-27b、qwen3.8-flash-next、codestral-2508/latest、codestral-embed/-2505、mistral-code-latest/fim-latest、mistral-embed/-2312、ministral-3b/8b/14b（latest+2512）、voxtral-mini/small（latest+dated）、mistral-large-4-0/-4（22:13 extend 补入）） |
| 映射 | 无（上游与本地 id 完全一致） |
| 姿态 | p20/w1，auto_ban=1；glm-5.2 池 ch15 p50 为主；26 模型 sole-carrier |
| abilities | 27 模型全部读回 `(default,1,20,1)` |
| 定价 | **只读对账未改写**：glm-5.2=2/3、glm-4.5-flash ModelRatio=0 已在库；其余 25 模型无条目走网关默认倍率（同 budsin glm-5.3 先例，非本次引入） |
| DB 快照 | `new-api-before-muyuan-gongyi-20261006-220441.db`（integrity=ok；resume 校验另产 `-220546.db`） |
| OMP/models.yml | 零改动（exact-id 透明冗余；2026-10-07 用户要求后接入 15 模型，见文末） |

## 验证

- admin 渠道自测：HTTP 200 success，0.92s，`ministral-3b-latest`。
- 网关链健康：`glm-5.2` chat 200（17/20）归因 **ch15**（既有主渠道，备份姿态行为级确认）。
- 唯一渠道实弹：`grok-4.7` 200（218/163）、`codestral-latest` 200（8/2）、
  `qwen3.8-27b` 200（57/20）、`mistral-large-4-0` 200（20/2）均归因 ch173
  （sole carrier，符合设计）。
- 22:14 门禁复跑（extend 后）：同一存量三 FAIL，ch173 0 次出现。
- 政策门禁 `newapi-local-smoke.py`（22:06）：FAILURES={channel model isolation,
  fallback channel posture, primary opus pool posture} —— 零新增违规。
  基线证据（`.tmp-newapi-dx-ops.log` 全量 8826 行）：
  `claude-fable-5-1-reversed` 隔离违规首现 **2026-10-04 15:25**；
  `45:missing` 首现 **2026-10-05 19:25**（fallback posture 规则自 2026-08-07 起
  断续 FAIL）；opus 主池姿态自 **2026-08-25** 起 FAIL（今日 ch3 另有 100xlabs
  fail-closed 授权禁用）。三个 FAIL 均早于 ch173（22:04）至少一天。
  **ch173/muyuan-gongyi 在全部违规行中出现 0 次**。
- OMP 门禁 `test_omp_routes.py`：40/40 OK。

## 被否设计（留痕）

1. **接入 `mistral-large-4-0`** —— 上游 400 model_price_error，硬接会产生
   死能力行并污染池健康；等上游定价后 resume 补入。
2. **"ModelRatio 缺条目 = 错误计费"硬断言** —— budsin 已否（glm-5.3 全站无条目
   走默认倍率为存量常态）；本次 23 模型同样只读报告。
3. **给 24 个唯一模型抬优先级** —— 无竞争者时优先级不改变路由，p20 保持全渠道
   统一备份姿态即可；glm-5.2 必须低于 ch15（p50），p20 满足。
4. **"本地必须设 ModelRatio 否则请求失败"** —— 混淆了上游/本地计费：
   `model_price_error` 是上游 muyuan.do 自己 NewAPI 的定价门禁；本地无
   ModelRatio 条目的模型按网关默认倍率**正常服务**（budsin 否设计 #3，
   glm-5.3 六渠道实证）。4 个模型无条目实弹 200 事后证明。
5. **移出 4 个 embed 模型** —— `test_model=ministral-3b-latest` 已设，渠道
   恢复探针只打 test_model，不会 chat 探 embed；embed 请求本就路由不到 chat。
   保留增加能力，移除反而要 PUT（禁带 status、GET 抹 key 勿回 PUT）徒增风险。

## 风险与备忘

1. 上游为共享公益站，计费/配额未知 → glm-5.2 保持备份位；26 个唯一模型无
   备选源，上游故障时这些模型随 auto_ban fail-closed（503 No available channel
   优于错误计费）。
2. 凭据经聊天明文传递（同 budsin/stepfun 前例，用户裁决不轮换）。
3. `mistral-code-fim-latest`/embed 系列为 FIM/嵌入模型，admin test_model 选用
   chat 型的 `ministral-3b-latest`；FIM/嵌入能力以 abilities 在册 + 上游清单
   为凭，未逐一实弹。
4. 与 ch83 `muyuan-sol`（Sol 链，status=2，固定路由契约）/ch119
   `muyuan-glm-5.2`（status=2）同域不同渠道，互不影响；guardian 的
   muyuan_sol_fallback 契约仅绑定 ch83。
5. 上游若再给新模型定价，编辑脚本 MODELS 并 `--apply --extend`（纯增量
   幂等）；定价（ModelRatio）需按官方价另补，防 37.5 默认倍率陷阱。
6. `grok-4.7` 为**重 reasoning 模型**：单字回复也烧 ~455 reasoning tokens
   （直连 10.3s，网关 5.6-99s 波动），共享池高峰可 504。已实证 22:26 一次
   网关 504 → 直连 200 → 网关重试 200（ch173 未 auto-ban，status=1）：
   瞬态容量问题非配置回归；该模型探针/脚本重试若偶发 504 属预期噪声。

## 回滚

禁用 ch173：`POST /api/channel/173/status {"status":2}` + abilities 随禁；
或还原 `backups/new-api-before-muyuan-gongyi-20261006-220441.db`（注意：会
同时回滚 22:04 之后的所有变更）。OMP 零改动无需回滚。

## 2026-10-07 OMP 接入（ch173 模型 → models.yml）

用户要求「muyuan 渠道的模型没接入 omp」，并再次提供该站连接信息；探测显示与在库
key 行为一致（同 27 项目录、同 tier 门、同 503），未轮换渠道 key，ch173/上游零改动。

### 探测（`scripts/ops/probe_muyuan_station.py`，只读；chat+tools 双探针）

- 上游 `/v1/models`（用户 key 直连）：HTTP 200，27 项，与 10-06 目录一致。
- 本地网关（zg-newapi 路径，19 个 chat 候选）：
  - **入库 15**：chat 全 200；tools 14/15 结构化 `tool_calls`（qwen3.8-27b /
    qwen3.8-flash-next / glm-4.5-flash / glm-5.2 / ministral-3b|8b|14b（latest+2512）/
    codestral-latest / codestral-2508 / mistral-code-latest / mistral-code-fim-latest）。
  - **grok-4.7**：chat 200（重 reasoning，ping 烧 ~315 rt，7-99s 波动）；tools 两轮未证实
    （1× 文本形态 `invoke tool probe_echo`、1× 504@60s）→ 注册 `supportsTools: false`，
    仅手动/文本用途。
  - **站侧不可用（排除；用户 key 直连复现）**：`glm-5.2-200k`、`glm-5.3-200k`
    503 `No available channel`；`mistral-large-4`、`mistral-large-4-0` 403
    `tier_not_allowed`（站侧订阅等级门；10-06 曾实弹 200，属站侧动态）。
  - **能力排除（8）**：4 embed（mistral-embed/-2312、codestral-embed/-2505）
    + 4 audio（voxtral-*）——OMP 无对应表面。

### 变更

- `~/.omp/agent/models.yml` zg-newapi 块追加 15 条（exact-id 直通；ch173 为其中 14 个
  唯一在营渠道，glm-5.2 主役 ch15/sensenova p50，该条走池）。备份
  `models.yml.bak-20261007-muyuan-ch173-omp`；commit `0187fc0`。
- contextWindow/maxTokens 为同族口径估算（站点未披露限额）。

### 验证

- YAML 解析 OK；CRLF 1016/1016；`omp models` zg-newapi = 76（61+15）。
- `test_omp_routes.py`：Ran 40 tests → OK（40/40，与 10-06 基线一致）。
- `omp -p --model zg-newapi/ministral-3b-latest` → `ok`（8.0s 含启动）；
  `omp -p --model zg-newapi/qwen3.8-27b` → `ok`（13.0s）——reasoning 条目路径实证。

### 回滚

`git -C ~/.omp/agent revert 0187fc0` 或还原 bak 文件；ch173 与上游零改动。
