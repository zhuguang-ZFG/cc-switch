# agentrouter 403 "user quota is not enough" 豁免 + sharedchat 双模型判断修复 — 2026-09-13

## 背景

用户反馈"agent 渠道使用中报错较多"。错误面统计（NewAPI 文件日志，跨
oneapi-20260910/11 两份）：

| 渠道 | 错误 | 次数 | 性质 |
|---|---|---|---|
| ch127 agentrouter-codex-gpt | 402 Budget pool | ~1605 | 投放制额度（已豁免） |
| ch45 agentrouter | 500 | ~735 | 上游网关抖动 |
| ch86 agentrouter-claude | 402 Budget pool | 422 | 投放制额度 |
| ch45 | 402 | ~394 | 投放制额度 |
| ch127 | 403 user quota is not enough | 93 | **投放制额度（未豁免，缺口）** |
| ch45/120 | 502 | ~180 | 上游抖动 |
| ch127 | 400 encrypted content | 13 | reasoning brick（scrub 急救） |

## 根因 1：ch127 的 403 文案不在豁免内（本次修复）

`codex_window_pool.is_window_budget_exhausted` 只匹配
"budget pool quota has been exhausted"。agentrouter 投放窗口外对 test/real
流量均可返回 403 "user quota is not enough"（账号时段配额，同 0/8/16 投放制）。
未豁免 → `scan_error_channels` 走软失败累积 → 阈值到达后误禁 ch127
（投放制额度被当渠道故障，禁用只会把投放点前后的路由打成空洞）。

**修复**：`window_quota_markers` 扩展为双文案（402 budget pool + 403 user
quota），豁免范围仍限 ch127（name+tag+auto_ban+host+models 五重校验），
其他渠道（ch86 agentrouter-claude 等）刻意不扩散——沿用 09-12 "deliberately
limited" 决策。

## 根因 2：is_sharedchat_pool 单模型判断失效（repo→线上同步）

线上版要求 `models == MODELS[0]`（仅 gpt-6-astra 单模型）；ch128 实际配置为
`gpt-6-astra,gpt-5.6-sol` 双模型 → `is_sharedchat_pool` 恒 False →
ch128 探针拒收 403（codex_access_restricted / 请使用最新版的codex客户端）
失去 `is_codex_probe_incompatible` 豁免。repo 版 09-12 01:01 已修（集合判断），
未同步线上。本次同步（该文件漂移方向与 test_guardian.py 相同：repo 领先）。

## 变更

- `~/.omp/guardian/codex_window_pool.py`：双补丁（+repo `scripts/ops/codex_window_pool.py` 同步后两端 identical）。
- 备份：`codex_window_pool.py.bak-20260913-userquota`（线上）。
- `test_guardian.py` 新增 `WindowBudgetQuotaTests` 5 用例：
  402 豁免 / 403 豁免 / 凭证类不豁免 / 豁免不扩散到其他渠道 /
  sharedchat 双模型 probe-incompatible（回归防护）。repo+线上同步。
- 引擎重启：schtasks `\NewAPI Guardian` /end（旧 PID 10272 确认死亡）
  → /run（新 PID 2932 @22:07:49，单实例复核通过），日志正常。

## 验证

- `test_guardian.py` 195 用例全绿（190 + 5 新）。
- 引擎 22:08 起 file-tail / opus 空响应率监控正常循环。

## 恢复 ch128 + ch86（2026-09-13 22:10，用户批准）

脚本：`scripts/ops/enable_ch128_ch86_20260913.py`（dry-run 默认；顺序**先 POST
status=1 再 PUT**——fork 的 abilities sync 按当前 status 推导 want_enabled，
反序 PUT 会把 abilities 重新 sync 成 0）。key 从本地 DB 读入进程内使用，不打印。

结果：
- 全库备份 `new-api-before-hashneuron-20260913-221240.db`（186MB，integrity ok）。
- ch128：status=1，abilities astra+sol 均恢复 enabled=1；管理探针 astra **ok**。
- ch86：status=1，abilities 5 行（opus-5/4-8 + zg×3）均恢复 enabled=1；
  探针 quota（投放窗口外，预期，不 gate）。
- 观察 2 个 Guardian 周期（22:12–22:15）：两渠道保持 status=1；
  ch128 探针拒收/额度类正确走豁免分支（"keeping bounded Codex failover
  eligible"）——`is_sharedchat_pool` 双模型判断修复后的首次现场生效验证。
- ch86 在投放窗口外由 NewAPI 探针记 402，Guardian normal quarantine 退避
  （quota 类封顶 15min），窗口内自动恢复入池，预期 flap，无需干预。
