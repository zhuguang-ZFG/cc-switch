# Zombie 停泊：ch127 `agentrouter-codex-gpt` + ch9 `linxi-k40`（2026-10-07）

## 结论

用户授权（"ch127/ch9/ch148 僵尸态处置…授权给你"）后执行。经取证：
- **ch127** 双模型上游死：`gpt-5.6-sol` → 503 `当前分组 default 下…无可用渠道`、
  `gpt-6-astra` → 402 `Budget pool quota has been exhausted`（agentrouter 侧预算池，用户充值才可愈）；
- **ch9** 死：`claude-opus-5` → 503 `No available accounts`、`claude-fable-5` → 404
  `not supported by any configured account`（linxi 同账号池，与 ch18 同源）；
- **ch148** 经复测当时**健在**（admin 200/8.0s，opus-5），**未处置**保持原样
  （注：当晚 02:10 起其 claude 预算池耗尽转 402，见「追加发现」）。

执行：**ch127 / ch9 → status=2 + auto_ban=1 停泊**（房式，ch172 先例；abilities 随禁）。
快照 `new-api-before-park-ch127-ch9-20261007-020731.db` / `-021031.db`（integrity=ok）。
脚本 `scripts/ops/park_zombie_channels_20261007.py`（dry-run 默认、身份+SSOT key 水合校验）。

## 关键机制实证（本轮新增）

1. **fork 的 admin `/status` 端点只接受 1/2，`{"status":3}` 返回 `Invalid parameters`**
   ——想手工把渠道压入「status=3 + auto_ban=1 → Guardian 自动恢复队列」不可行；
   status=3 只能由 NewAPI 请求路径的 auto-disable 产生（且本机该开关全局关闭）。
   自愈轨道入口关闭 → 采用「停泊 + 文档化手动复活」。
2. **smoke 的禁用归因公式**（`newapi-local-smoke.py`）：
   `unexpected = 未在 (KNOWN_BROKEN ∪ DEGRADED_ACCEPTED_DISABLED ∪ guardian_disabled_ids) 且
   (status==3 或 (status==2 且 auto_ban≠1))`。
   → **status=2 + auto_ban=1 = 免标记停泊配方**（ch172/ch118 先例一致）。
3. **双死跳链问题**：RetryTimes=1 下，opus-5 链若连续两跳皆死（ch9+ch18 同账号），
   请求会在两个 503 间耗尽重试并**原样上浮 503**（非 failover 到 p-20 备份）；
   停泊 ch9 后链首为 ch18（仍在 AutoEnable↔Guardian 拉锯、死亡中），
   消息/聊天面走向取决于单次重试能否落到 ch148/177 —— 见「后续」。
4. **ch18 拉锯留痕**：`guardian.log` 01:45 `Channel 18 … auto-enabled before stable;
   disabled again` —— `AutomaticEnableChannelEnabled=true`（单次测试通过即复活）
   与 Guardian 稳定性回滚互相抢权，ch18 会周期性以死态回到链首。

## 变更表

| 渠道 | 前姿态 | 后姿态 | abilities | 复位条件 |
|---|---|---|---|---|
| ch127 agentrouter-codex-gpt | status=1 p40 w5 auto_ban=0 | **status=2 p40 w5 auto_ban=1** | gpt-5.6-sol=0, gpt-6-astra=0 | agentrouter 预算池充值 + sol/astra 复测 200 → `POST /api/channel/127/status {"status":1}` |
| ch9 linxi-k40 | status=1 p52 w10 auto_ban=0 | **status=2 p52 w10 auto_ban=1** | claude-opus-5=0, zg-claude-opus-5=0 | linxi 账号池回血 + admin 复测 200 → `POST /api/channel/9/status {"status":1}` |
| ch148 budsin-apichat | status=1 p-10 w1 auto_ban=1 | **未动（活体）** | — | — |

## 门禁与回归（02:16 实测）

- `newapi-local-smoke.py`（02:16）：FAILURES={channel model isolation,
  fallback channel posture, primary opus pool posture}——**与基线同三项、
  零新增**；`channels — total=100 enabled=36 unexpected_disabled=none`
  （ch127/ch9 因 auto_ban=1 免标记，配方成立）；`primary opus pool posture`
  违约文本按预期变为 `['3:baibei-100xlabs=priority=54,weight=12',
  '9:linxi-k40=status=2,weight=10']`；`pool capacity claude-opus-5 —
  enabled_channels=3 min=2 ids=[18, 148, 177]`。
- 网关回归探针（02:10-02:15）：
  - `gpt-5.6-sol` → **200 attr=176**（ch127 停泊后由 p-20 层服务 ✓）
  - `gpt-6-astra` → **200 attr=175** ✓
  - `claude-opus-5`（messages/chat 双面）→ **402 `Budget pool quota`** ——见下

## 追加发现：opus-5 全链告急（02:10 起）

停泊 ch9 后探针揭示 opus-5 三个在营承载**当前全部不可服务**：
- ch18 linxi（enabled）：503 `No available accounts`（与 ch9 同账号死因，且处
  AutoEnable↔Guardian 拉锯）；
- ch148 budsin（enabled）：**claude 预算池耗尽 → 双面 402 `Budget pool quota`**
  （02:00 其 admin 复测还是 200/8s；系上游预算池新耗尽，非本地配置）；
- ch177 asvla（enabled）：key 余额耗尽（402→403）。

结论：opus-5 双面当前 402/503 直出（两类码均不在 AutomaticRetryStatusCodes，
不 failover）。属**外部恢复项**：budsin 预算池充值/重置、linxi 账号池回血、
asvla 充值。本地无正当动作（ch148 另承载 deepseek-v4-flash/glm-5.3/k3/
step-3.7-flash/qwen3.8-max 五模型，不可整体停泊）。

## 回滚

- 单渠道复位：`POST /api/channel/{127,9}/status {"status":1}`（abilities 随启；
  auto_ban=1 保留，与其它自动化管理渠道一致）。
- 批次还原：`~/.new-api-local/backups/new-api-before-park-ch127-ch9-20261007-021031.db`
  （会回滚该时刻后的所有变更）。

## 遗留与建议

- **ch18 `linxi-k40-opus5-backup`（不在授权名单）**：与 ch9 同账号同死因，
  仍处 enabled，构成 opus-5 链首死跳。建议二选一：同法停泊，或等待 linxi 回血
  （若回血，ch9+ch18 一起复活最省事）。**需用户明示**。
- **ch148 claude 腿预算耗尽**（02:10 起 messages/chat 双面 402，`Budget pool
  quota`，budsin 侧预算池）：详见「追加发现」；属外部恢复项（充值/重置），
  非本地可修（ch148 其余 5 模型腿不受影响）。
- **asvla ch177** 余额耗尽（402→403），与 ch9 停泊无关；充值后重跑
  `add_asvla_channel.py --apply`。
