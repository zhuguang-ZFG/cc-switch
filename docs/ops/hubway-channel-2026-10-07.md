# hubway.cc 渠道接入（ch175）（2026-10-07）

## 结论

用户提供 `https://hubway.cc/v1` + key（走 `HUBWAY_KEY` env，不落盘不回显）。
接入完成：新建 **ch175 `hubway`**（type=1，**p-20**/w1，auto_ban=1，
test_model=`gpt-5.6-terra`），7 个 live-probed 模型
（codex-auto-review / gpt-5.5 / gpt-5.6-sol / gpt-5.6-terra / gpt-6-astra /
gpt-6-sol / gpt-6.1-sol）。

接入过程实证两条关键状态：

1. **ch127 `agentrouter-codex-gpt` 上游已死**（并非本轮变更，属发现）：
   `gpt-5.6-sol` admin test → 503 "当前分组 default 下对于模型 gpt-5.6-sol 无可用渠道"，
   `gpt-6-astra` → 402 "Budget pool quota has been exhausted"。ch127 仍
   `status=1`（启用态≠可用态），路由自动跳过并落到 p-20 备份。
2. 因此 gpt-5.6-sol / gpt-6-astra 的现役服务者实为 **ch175**（网关实弹
   attr=175）；gpt-5.6-terra 主渠道 ch174 健康，attr=174 不变。

同日配对的 **ch176 `vsakura-gpt`**（apisub.vsakura.top key#3，4 模型，
同 p-20 档）见 `docs/ops/vsakura-gpt-channel-2026-10-07.md`——两渠道共享
sol/terra/astra/6-sol 的 p-20 服务位。

## 上游探测证据（只读，key 不落盘）

- `GET /v1/models` → HTTP 200，8 ids（含 gpt-5.6，见下排除）。
- 直连实弹（chat/completions）：7 模型非流全 200；SSE 抽测 gpt-6-sol
  200（5 data 帧 + [DONE]）。
- **排除 gpt-5.6**：首探 502，复探 400
  `The 'gpt-5.6-sol' model is not supported when using Codex with a ChatGPT
  account` —— 上游把 gpt-5.6 别名映射到 gpt-5.6-sol 且落在
  Codex/ChatGPT 账号池，死别名。回加条件=直连 chat 200 + `--extend`。
- **UA matrix**：`curl`/`Go-http-client/1.1`/空 UA 全 200（CF 只 ban
  python-urllib UA）→ NewAPI Go 客户端无需 header_override。
- **conformance canary**（`probe_untrusted_openai_provider.py --run`）：
  - 轮 1（sol/terra/6-sol，10 请求）：**0 issue**；tool call valid；
    stream/nonstream/usage 全绿；cache 共享前缀命中
    （suspicious_first_request_cache_hit=true，套壳池共性）。
  - 轮 2（astra/6.1-sol + tool=astra，8 请求）：**0 issue**，tool valid。
- 延迟特征（非流，小请求）：terra 14.2s（最快）；astra 单发可到 33s；
  sol 6.9-32.9s 波动。适合后台/备份用途。
- 身份指纹：`resp_*` 完成 id + `service_tier` 字段 → OpenAI 兼容套壳
  （与 ch174 同类型账号池）。

## 变更

执行脚本：`scripts/ops/add_hubway_channel.py`
（key 走 `HUBWAY_KEY` env，dry-run 默认，幂等 resume，纯增量 `--extend`）。

| 项 | 值 |
|---|---|
| 渠道 | **ch175 `hubway`**（type=1，base `https://hubway.cc` 不带 /v1） |
| 模型 | codex-auto-review、gpt-5.5、gpt-5.6-sol、gpt-5.6-terra、gpt-6-astra、gpt-6-sol、gpt-6.1-sol（exact-id） |
| 姿态 | **p-20**/w1，auto_ban=1（低于 ch127 p40 与 ch174 p-10） |
| abilities | 7 行全部读回 `(default,1,-20,1)` |
| 定价 | 只读对账未改写：sol/terra/5.5/codex-auto-review 已有 0.5/2；astra/6-sol/6.1-sol 无条目走网关默认 |
| DB 快照 | `new-api-before-hubway-20261007-004717.db`（26206208 bytes，integrity=ok） |
| OMP | models.yml 新增 gpt-6-sol / gpt-6.1-sol / gpt-6-astra 条目（默认 openai-completions），terra 条目 name 更新（不再 sole carrier） |

## 关键机制实证（本轮新确认）

1. **启用态≠可用态**：ch127 `status=1` 但 sol 503 / astra 402——NewAPI
   路由会跳过上游全失败的渠道，自动落到下一 priority 层；对这类"僵尸主
   渠道"，contested 断言必须跟随**live 探测结果**而非 status 字段。
2. **contested 断言自适应**（脚本设计变更）：对每个 contested 模型先
   `primary_serves()`（admin test 实测主渠道），主活→断言 attr==主；
   主死→断言 attr∈p-20 层（ch175/ch176）。主渠道恢复后严格模式自动回归。
3. p-20 同层双渠道（ch175/ch176）对共享 id 由 NewAPI 按 weight 随机
   选择——两源皆为套壳备份，视作等价腿。

## 验证

- admin 渠道自测（ch175）：terra 200/15.61s；sol 200/6.887s；
  astra 200/28.083s（后端复测记录）；终跑 terra 200/2.472s。
- 终跑（exit 0，全断言过）：contested `gpt-5.6-sol`→ch175、`gpt-5.6-terra`→ch174、
  `gpt-6-astra`→ch175；sole `codex-auto-review`/`gpt-5.5`/`gpt-6.1-sol`→ch175
  （gpt-6.1-sol 上游 502 窗口恢复后 200）；shared `gpt-6-sol`→**ch176**
  （p-20 层随机，符合设计）。
- abilities 7/7 读回 `(default,1,-20,1)`；read-back models=7。
- canary 两轮 0 issue（含 tool）。
- 快照 integrity=ok。
- 政策/路由门禁结果见文末「门禁」节（与 vsakura/asvla 一并跑）。

## 被否设计（留痕）

1. **contested 严格"attr==主渠道"（照抄 grok 模板）**——否：ch127 上游
   死亡期间误报（首次 --apply 在此中断，ch175 已建但验证未过）。改为
   live 状态自适应后 resume 通过。
2. **依赖 status 字段判断主渠道可用性**——否，见机制实证 #1。
3. **注册 gpt-5.6**——否，上游别名死（400）；恢复条件见上。
4. **OMP 条目仿 luna 家族用 `api: openai-responses`**——否：chat/SSE/
   tool canary 均绿，且无任何 responses 面证据；用默认
   openai-completions。
5. **gpt-5.5 / codex-auto-review 入 OMP**——本轮否：gpt-5.5 属老代、
   codex-auto-review 语义未明（"Codex Auto Review"），观察名单；需要时
   单独加条目。

## 风险与备忘

1. **共享/免费 key 性质未知**（用户提供）：限流/配额不可预期；p-20
   备份档 + auto_ban=1 fail-closed；耗尽/失效由 Guardian 扫描处理。
2. **套壳身份未明**：响应注入约 4.4k 前缀 token（共享缓存跨用户命中），
   计费按本地 ModelRatio（0.5/2 已有条目）。
3. **慢响应**：astra 非流可到 33s；如后续做实时角色需评估超时预算。
4. **gpt-6.1-sol 仅本渠道在营**（vsakura 侧 3×503）；gpt-6-sol 与
   ch176 同层随机。ch127 恢复时 sol/astra 自动回到 ch127 主（严格断言
   回归，可用脚本重跑确认）。
5. 凭据经聊天明文传递（同前例，用户裁决不轮换）。

## 回滚

- 禁用：`POST /api/channel/175/status {"status":2}`（abilities 随禁）。
- 还原 DB：`~/.new-api-local/backups/new-api-before-hubway-20261007-004717.db`
  （会同时回滚该时刻之后的所有变更）。
- OMP：`~/.omp/agent` 仓 revert 对应 models.yml commit。

## 门禁（2026-10-07 01:44-01:46）

- `newapi-local-smoke.py`：FAILURES = {channel model isolation（ch72 存量）、
  fallback channel posture（45:missing 存量）、primary opus pool posture
  （3/9/18 存量）}——与前夜 22:06/22:14/23:13/23:25 基线**逐字相同**，
  ch175/176/177 **零新增违规**；`channels total=100 enabled=36
  unexpected_disabled=none`；`pool capacity claude-opus-5 … ids=[9,148,177]`。
- `test_omp_routes.py`：**40/40 OK**（含本批新增 models.yml 条目），
  热加载后 `omp models` 可见新条目。
- 备注：ch127 僵尸态（sol 503 无可用渠道 / astra 402 budget pool）属存量
  问题，处置需用户授权，本轮未动；网关失败转移已实证可落到 p-20 备份
  （sol/astra attr=175/176）。asvla（ch177）收尾期 key 余额耗尽（402），
  见其 runbook。
