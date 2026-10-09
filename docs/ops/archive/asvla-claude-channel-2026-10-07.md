# asvla.bbqwq.com 渠道接入（ch177 `asvla-claude`）（2026-10-07）

## 结论

用户提供 `https://asvla.bbqwq.com/v1` + key（“claude满血全系列”，走 `ASVLA_KEY`
env，不落盘不回显）。接入完成：新建 **ch177 `asvla-claude`**（**type=14**，
**p-20**/w1，auto_ban=1，test_model=`claude-opus-5`），10 个 Claude 模型
（fable-5 / fable-5-1 / haiku-4-5 / opus-4-6 / opus-4-7 / opus-4-8 / opus-5 /
opus-5-5 / sonnet-4-6 / sonnet-5）。

价值定位：**opus-4-6 / opus-4-7 / sonnet-4-6 / sonnet-5 在本地池零渠道**
（asvla 为唯一来源，入列即可用）；fable-5（ch9 上游死）/ fable-5-1（ch3 主）
由 asvla 补位；haiku-4-5 / opus-4-8 / opus-5 / opus-5-5 增加备份容量。
OMP models.yml 新增 6 条目（详见「OMP」节）。

## 上游探测证据（只读，key 不落盘）

- `GET /v1/models` 37 ids（catalog 含 gpt-* 与 image 项，见排除）；Claude 全系
  直连 200（2.2-5s）：opus-5 / opus-5-5 / sonnet-5 / fable-5 / fable-5-1 /
  opus-4-8 / opus-4-6 / opus-4-7 / sonnet-4-6 / haiku-4-5
- `/v1/messages`：`x-api-key` 与 `Bearer` 均可通过；anthropic SSE
  （`message_start` → `message_stop`）验证 200
- canary（`probe_untrusted_openai_provider.py --run`）：**0 issue**；
  tool 探针（nested tool_choice + stream）**通过**——与 vsakura 的缺口形成对照
- 无注入 token（prompt 29-33 tokens）；无 UA 门（curl / Go / 空 UA 全 200）
- 品牌自报 “Astra由BBcloud提供” / owned_by=sub2api

## 池姿态（接入时）

- opus-5：ch9（p52）上游死（`No available accounts`）；ch148（p-10）服务
- opus-5-5 / fable-5-1：ch3（p54）服务；fable-5：仅 ch9（死）
- opus-4-8：ch95（p50）服务；haiku-4-5：ch69/ch68 服务
- opus-4-6 / opus-4-7 / sonnet-4-6 / sonnet-5：**零渠道**
- ch177 落 p-20（type=14，同 ch9/ch3/ch123 形态；低于全部启用主渠道）

## 变更

执行脚本：`scripts/ops/add_asvla_channel.py`（`ASVLA_KEY` env，dry-run 默认，幂等 resume）

| 项 | 值 |
|---|---|
| 渠道 | **ch177 `asvla-claude`**（type=14，base `https://asvla.bbqwq.com` 不带 /v1） |
| 模型 | 10 个 Claude id（exact-id，无映射） |
| 姿态 | **p-20**/w1，auto_ban=1 |
| abilities | 10 行 `(default,1,-20,1)` 读回 |
| 定价 | 只读对账未改写：9/10 已有 0.5/2；fable-5-1、opus-5-5 无条目走网关默认 |
| DB 快照 | `new-api-before-asvla-claude-20261007-011306.db`（26480640 bytes，integrity=ok） |
| OMP | models.yml 新增 opus-4-6 / opus-4-7 / sonnet-4-6 / sonnet-5 / fable-5 / fable-5-1 六条目（zg-newapi-anthropic，默认 anthropic-messages 继承） |

## 验证

- admin 自测（ch177）：opus-5 200/5.646s、复测 2.076s/2.471s/2.804s；fable-5 200/2.854s
- 网关实弹归因（openai 面，接入期平衡窗口内）：
  - `claude-opus-4-8` → ch95（主活，primary-keeps ✓）
  - `claude-opus-5` → ch148（primary-keeps ✓）
  - `claude-fable-5` → ch177（ch9 死，takeover ✓；两次 200 attr=177）
- abilities 10/10 `(default,1,-20,1)`；read-back models=10；快照 integrity=ok
- OMP 静态门禁 `test_omp_routes.py`：**40/40 OK**（含 6 新条目）
- 政策门禁 `newapi-local-smoke.py`（01:44）：FAILURES={channel model isolation,
  fallback channel posture, primary opus pool posture} 与前夜基线逐字相同 =
  **零新增**；`pool capacity claude-opus-5 — enabled_channels=3 min=2 ids=[9,148,177]`
- **收尾期 upstream 余额耗尽（重要）**：最终 sweep 时 asvla 对全部模型返回
  **402 `当前 API Key 所属账户余额不足，请充值后重试`**（opus-4-6 与 opus-5 对照
  双双 402，属账户级状态）。余下未完成的网关断言失败均可归因于此 + 存量渠道
  抖动窗口，非姿态缺陷：
  - fable-5-1：ch3 502 窗口 + asvla 402（此前 admin 200/2.854s + 直连 200 已留证）
  - opus-5-5：ch3 502 窗口 + asvla 402（此前直连 200 + ch3 admin 200）
  - haiku-4-5：agnes 免费池 429（“免费用户的 API 速率限制”，主渠道限流未 failover）
  - opus-4-6/4-7/sonnet-4-6/sonnet-5（零渠道系）：直连 200 已证；网关断言待充值后重跑
- fail-closed 姿态：402 不触发 failover（不在 `AutomaticRetryStatusCodes`）；
  `余额不足` 命中 Guardian 关键词扫描 → 将自动隔离入恢复队列；充值后凭恢复
  流程/`status` 端点复活

## 被否/排除（留痕）

1. **接入 catalog 中 gpt-* 项** —— 否：实弹 `model_not_found`（catalog 与实配不一致）
2. **haiku-4-5-20251001** —— 403 server_error 排除
3. **OMP 用 openai-responses** —— 否：chat + messages 双面实弹均过；沿用 provider
   默认（anthropic-messages）
4. **首跑 contested 在 fable-5 处 404 判定为“模型不可路由”** —— 否：abilities
   缓存竞态（channel/fix 后立即打网关）；复测 200 attr=177。脚本已加
   `gateway_chat_retry`（404/429/5xx/524 有界 4 次重试）与 admin test 重试环

## 风险与备忘

1. **key 余额已于接入当日耗尽**（01:40 观测 402 `余额不足`）：充值前 ch177 不可用
   （fail-closed；Guardian 扫描将隔离、充值后恢复）；sub2api 套壳配额口径未知
2. **存量池问题（非本渠道）**：ch9 对 Claude 系 `No available accounts`；
   ch148 的 anthropic 面 opus-5 曾 402（`Budget pool quota`，402 不在
   `AutomaticRetryStatusCodes`，/v1/messages 面可能被 402 截断而未 failover 到
   ch177）——ch148/ch9 处置属路由变更，需用户授权/Guardian 流程，未动
3. 凭据经聊天明文传递（同前例，用户裁决不轮换）

## 回滚

- 禁用：`POST /api/channel/177/status {"status":2}`（abilities 随禁）
- 还原 DB：`~/.new-api-local/backups/new-api-before-asvla-claude-20261007-011306.db`
- OMP：revert `~/.omp/agent` commit `c1b56aa`（删除 6 条 Claude 条目）
