# intern-discovery 池扩充（+ch146 k5 / ch147 k6）（2026-10-03）

## 结论

用户提供两把**新** InternAI key（sha256 比对确认与池内全部既有 key 不同）。
官方源此前已是单 key 一渠道池：ch140 `intern-discovery` + ch141/142/143
`-k2/-k3/-k4`（status=1，prio=40，w=1）。按同模式扩 **ch146 `-k5`、ch147 `-k6`**，
逐 key/逐渠道硬门全过。池现 6 key。

## 既有池事实（只读查明）

- 每渠道 models：`glm-5.3, intern-s2, deepseek-v4-flash-vision, qwen3-8-27b`，prio=40/w=1。
- 上游目录（10）：`Agents-A1, Atria-Dawn-Preview, deepseek-v4-flash-0731,
  deepseek-v4-flash-vision, deepseek-v4-pro-0813, glm-5.3, intern-s2, kimi-k2.6,
  minimax-m3, qwen3.8-27b`。
- **`qwen3-8-27b` 拼写不一致（既有问题，未动）**：池渠道载 `qwen3-8-27b`，上游目录
  实为 `qwen3.8-27b`——该 ability 实为死映射，后续应统一口径（不在本轮范围）。
- 转发档（deepseek/glm/kimi 等）**不接入**，避免与既有渠道路由碰撞。
- 池功能预检：网关 intern-s2 → ch141、glm-5.3 → ch141（真 usage）。

## 变更

执行脚本：`scripts/ops/add_intern_discovery_pool_keys.py`（key 走
`INTERN_AI_KEY[_2]` env，dry-run 默认，幂等：sha256 比对跳过已在池 key，
命名自动接续 kN）。取代本轮早些时候的 `add_intern_ai_official_channel.py`
（已删除——其 fail-closed 设计在发现既有池后不再适用）。

| 项 | 值 |
|---|---|
| 新渠道 | ch146 `intern-discovery-k5`、ch147 `intern-discovery-k6`（type=1，base 不带 /v1） |
| 模型 | `glm-5.3, intern-s2, deepseek-v4-flash-vision`（目录验证子集；拼写存疑的 qwen 未带） |
| 姿态 | priority 40 / weight 1（对齐池），auto_ban=1 |
| abilities | 两渠道 ×3 模型均 `(default,1,40,1)` |
| 定价 | ModelRatio 未设（open item） |
| DB 快照 | `new-api-before-intern-pool-keys-20261003-175358.db`（integrity=ok） |

## 硬门验证（pool 级归因不能证明新 key——设计要点）

1. sha256 幂等：两 key 均为池外新 key；
2. **逐 key 直连上游 chat**：15/242、15/203 双双 200（挂 key 会被排除且零写入）；
3. abilities 形状 ×2 渠道 ×3 模型；
4. **逐渠道 admin test** `/api/channel/test/{146,147}` success=true；
5. 网关 rotation note：intern-s2 200 归因 ch140（旧成员——仅健康参考，不作新 key 证据）。
6. 后续网关真实流量：OMP `intern-s2` 请求命中 **ch146**（日志 217113，46547/514，
   type=2）——新成员已入轮询（ch147 暂无随机命中，6 渠道同 prio/w 属预期，
   其有效性由 gate 2 直连 + gate 4 admin test 承载）。

## 风险与备忘

1. `glm-5.3` 池扩容直接增厚 advisor 主模型的官方货源（当日 zg-newapi 侧 429 的背景）。
2. 凭据经聊天明文传递，用户裁决**不轮换**（已记录）。
3. `Agents-A1`（平台自有 agent 模型）未接入，如需可复跑脚本扩 POOL_MODELS。
4. 本轮 transcript 另有 zg-newapi-anthropic gateway token 两次暴露（L401/L597），
   用户裁决不轮换，**未闭环**记录于此。

## 回滚

禁用 ch146/ch147（双表），或还原 `backups/new-api-before-intern-pool-keys-20261003-175358.db`。
