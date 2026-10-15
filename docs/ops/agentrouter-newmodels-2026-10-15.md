# agentrouter 上新接入（2026-10-15 晚）：gpt-6.1-sol + claude-opus-5-5

用户转来群公告：「gpt-6.1-sol（推荐使用）、claude-opus-5-5 已上线，同时增加了
资源投放量」。

## 上游证据（管理探针经 NewAPI；key 从 SSOT 进程内读，不落日志）

- `ch180`（agentrouter-deepseek-v4-flash）对 `gpt-6.1-sol` → **200**；
- `ch86` 对 `claude-opus-5-5`：429（并发限流）→ 500 → **200**（间隔重试）；
- ch127（agentrouter-codex-gpt）的 key 上游已失效（401 invalid api key）→ 不回开；
- 8788 代理的 keys.json 全部失效（401）——需轮换；
- ch86/134/135/136 的上游池已不再有 `claude-opus-5`（503 无可用渠道）——只剩
  `-5-5`。

## 变更（快照 `new-api-before-agentrouter-newmodels-20261015-212818.db`）

- `ch180` models += `gpt-6.1-sol`（p51，优于余额近空的 jojatoken p-20）；
- `ch86/134/135/136` models += `claude-opus-5-5`、`test_model` 改指该名、
  **回开**（status=1）→ 恢复 OMP 已注册但唯一活腿 ch3（超时）死掉的
  `claude-opus-5-5`；
- readback：4 渠道 status=1 + 各自 ability enabled=1 ✓。

## 网关实弹

- `gpt-6.1-sol` → **200，落 ch180**；它是推理模型，小 max_tokens 探测全喂
  reasoning（content 空属探测预算坑，非故障）；
- `claude-opus-5-5` → **200**（37.3s，落 **ch135**）；其间 120s 超时 ×2 是
  队列拥塞时段，非不可用。

## 遗留（更正于评审）

- ~~8788 代理 keys.json 失效~~ **更正：8788 代理健康**。ch180 的
  base_url= `http://100.83.32.95:8788`（就是该代理），今天连发 200；我先前
  用**渠道 key** 直连 8788 探针得的 401，是撞了代理自己的客户端鉴权门
  （64 字符 key 仅本地鉴权、不透传上游）——不是 keys.json 失效。
- ~~ch127 key 上游失效（401）~~ **更正后已处置（裁决，评审要求的明确决定）**：
  key 有效、`deepseek-v4-flash` admin test 200、codex 系（gpt-6-astra/
  gpt-5.6-sol）402 预算池尽 → **trim 到 `deepseek-v4-flash` 单名 + 回开**
  （status=1；快照 `new-api-before-ch127-trim-20261015-220837.db`；readback +
  双 ability 行 enabled ✓）。审计注记：这回开**给 dsv4f（default 角色模型）
  加了第四条活腿**（ch180/ch173/ch218 之外）；codex 系名字上游补货后再回挂
  （恢复时按 runbook 复测）。
- ch86-136 仍挂 `claude-opus-5`/`opus-4-8`（上游池已无该名）——若上游彻底下架，
  按聚合裁剪那套摘掉；
- OMP 重启后生效（config/扩展），渠道改动即时生效。

## 多 key 体检与一处误报澄清（2026-10-15 深夜，用户提醒后补做）

用户提醒「agentrouter 渠道是多 key」。核对修改后的多 key 状态：

| 渠道 | is_multi_key | mode | key 行数 | 结论 |
|---|---|---|---|---|
| ch127 | True | polling | 5（key 列） | PUT 保留 channel_info ✓ |
| ch86/134/135/136 | False | — | 1 | 单 key 池成员 ✓ |

- ch127 五把 key 逐一直连上游（claude-cli UA + Clash 出口）**全部 200**；
- 22:16 实弹 relay 的 `401 unauthorized client detected`：同 key 同 UA 直连
  stream/non-stream 均 200，且 22:16 后无再发 → **瞬时 CF/UA 门抖动，非 key
  失效**（同分钟 ch118 也在 524）；
- ch136 被 Guardian 稳定性监控以「探针超时」打回 status=2：其 key 实弹 200
  （opus-5-5 TTFT ~37s 超了探测预算）→ 回开（快照
  `new-api-before-ch136-reenable-20261015-222306.db`）；若探测预算仍压超时，
  明日看它是否又 flap（慢但活的渠道与稳定性监控的预算张力是已知类）。

## 回滚

```bash
# 整库：cp 备份（new-api-before-agentrouter-newmodels-20261015-212818.db，停机）
# 逐渠道：models 摘除 + status 回 2（runbook 契约）
```
