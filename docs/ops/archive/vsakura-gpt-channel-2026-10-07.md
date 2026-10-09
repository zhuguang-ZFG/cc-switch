# apisub.vsakura.top key#3 渠道接入（ch176 `vsakura-gpt`）（2026-10-07）

## 结论

用户提供“深夜福利”贴（含 3 把 key）。接入完成：**仅 key#3（GPT sol 系）可用** ——
新建 **ch176 `vsakura-gpt`**（type=1，**p-20**/w1，auto_ban=1，
test_model=`gpt-5.6-terra`），4 模型（gpt-5.6-sol / gpt-5.6-terra / gpt-6-astra /
gpt-6-sol）。key#1（Claude 系）与 key#2（GLM 系）各自死因明确，未接入。

与同日 ch175 `hubway` 构成 **p-20 双备份层**：四个共享 id 由 hubway + vsakura
同级随机服务（hubway 脚本以 `VSAKURA_CARRIES` 感知该共享位）。

## 三把 key 的分档（同帖）

| key | 模型组 | 实弹结论 | 处置 |
|---|---|---|---|
| #1 `sk-ff36…` | Claude 系 10 模型 | chat 403 `All available accounts exhausted`（sonnet-5/opus-5/haiku-4-5 逐验）；messages 面 502 `Upstream access forbidden`（sonnet-5/fable-5） | 未接入（账号池尽）。恢复条件：`/v1/messages` claude-sonnet-5 → 200 |
| #2 `sk-9153…` | GLM 系（5.1/5.2/5.3/5.3-flash） | chat / messages / responses（±stream）四形态全 400 `This group does not allow cross-protocol conversion` | 未接入（站点分组协议封锁；四向穷举证明非请求格式问题）。恢复条件：chat glm-5.3 → 200 |
| #3 `sk-bf12…` | sol/astra 系 | 4 模型 200；gpt-6.1-sol 3×503；image 无 chat 面 | **本渠道**（4 模型） |

## 上游探测证据（只读，key 不落盘）

- `GET /v1/models` 7 ids；直连实弹：sol 2.6s / terra 7.0s / astra 3.4s / 6-sol 2.7s（resp_* id 转换套壳）
- SSE：5 data 帧 + `[DONE]`
- canary（`probe_untrusted_openai_provider.py --run`）：stream/非流/缓存全绿；
  **tool 探针 400** —— nested tool_choice（`{"type":"function","function":{...}}`）
  被上游拒（`[ObjectParam] [tool_choice.name] [missing_required_parameter]`）；
  同一 tools 下 string 形态（`"required"`）与缺省均 200 / tool_calls 正常
  → 边界=「不支持标准 nested tool_choice 对象」，非 tools 不可用。
- 瞬态实录：429 `Upstream rate limit exceeded`（重试即过）；
  `gateway_concurrency_limit`（站点 per-user 并发限制，SSE 中段事件形态）
- 计费特征：转换层注入 ~3.5k prompt token/次（“Say ok” 亦 3564/5，~80% 共享缓存命中）
  → 配额放大 ~100x；套壳共性，已留痕
- UA 无门（curl / Go-http-client / 空 UA 全 200）

## 变更

执行脚本：`scripts/ops/add_vsakura_gpt_channel.py`（`VSAKURA_KEY` env，dry-run 默认，幂等 resume）

| 项 | 值 |
|---|---|
| 渠道 | **ch176 `vsakura-gpt`**（type=1，base `https://apisub.vsakura.top` 不带 /v1） |
| 模型 | gpt-5.6-sol、gpt-5.6-terra、gpt-6-astra、gpt-6-sol（exact-id） |
| 姿态 | **p-20**/w1，auto_ban=1（低于 ch127 p40 / ch174 p-10） |
| abilities | 4 行 `(default,1,-20,1)` 读回 |
| 定价 | 只读对账：sol/terra 已有 0.5/2；astra/6-sol 无条目走网关默认 |
| DB 快照 | `new-api-before-vsakura-gpt-20261007-005920.db`（26365952 bytes，integrity=ok） |
| OMP | gpt-6-sol / gpt-6-astra 条目随 ch175 批次入册（同一份 models.yml 变更） |

## 验证

- admin 自测：terra 200 / 3.45s（首跑）；终跑 200 / 21.24s（并发限流窗口释放后）
- 网关实弹归因（终跑 exit 0，四条断言全过）：gpt-5.6-sol → ch176（p-20 层）；
  gpt-5.6-terra → ch174（主活）；gpt-6-astra → ch176（p-20 层）；
  gpt-6-sol → ch176（p-20 层）
- 快照 integrity=ok（终跑另存 `new-api-before-vsakura-gpt-20261007-012008.db`）
- 门禁（与 ch175/ch177 一并跑）：`newapi-local-smoke.py` 仅存量 3 FAIL（逐字同基线）零新增；
  `test_omp_routes.py` 40/40 OK

## 被否/排除（留痕）

1. **K1/K2 强行接入** —— 否：账号池尽 / 分组协议封锁；接入即污染扫描与 auto_ban
2. **注册 gpt-6.1-sol** —— 3×503 排除（hubway 侧在营，可择机 `--extend` 回补）
3. **注册 gpt-image-2/2.5** —— 无 chat 面证据，未验证不注册
4. **期望 429 自动 failover** —— 否：429 不在 `AutomaticRetryStatusCodes`（408,500-503），客户端退避 / 备份层兜底

## 风险与备忘

1. 免费共享 key（深夜福利贴）：限流、并发、配额不可预期；p-20 + auto_ban=1 fail-closed
2. nested tool_choice 缺口：强制指定单一工具形态的调用会被 400；OMP 默认流（tools、无 forced choice）不受影响
3. 注入 token 放大：计费口径按本地 ModelRatio（0.5/2 已有条目）
4. 与 ch175 共享四 id：同级随机，视作等价备份腿
5. 凭据经聊天明文传递（同前例，用户裁决不轮换）

## 回滚

- 禁用：`POST /api/channel/176/status {"status":2}`（abilities 随禁）
- 还原 DB：`~/.new-api-local/backups/new-api-before-vsakura-gpt-20261007-005920.db`
- OMP：无单独变更（条目随 ch175 批次，回滚见该 runbook）
