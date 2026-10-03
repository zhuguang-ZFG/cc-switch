# StepFun Step Plan 恢复接入（ch144：step-3.7-flash + step-router-v1）（2026-10-03）

## 结论

用户提供 Step Plan key（base64 编码）与端点 `https://api.stepfun.com/step_plan/v1`。
历史 ch36（2026-07-31，因"无订阅 400"禁用后已删除）无本地残留，新建 **ch144
`stepfun-step-plan`**（type=1），在册启用，网关实弹 200 归因 ch144，OMP E2E
`omp -p --model zg-newapi/step-3.7-flash` → `STEP37_OK`。

## 实证（直连用户 key，env 注入，不落盘）

- `GET /step_plan/v1/models`：`step-3.7-flash`（262144 输入、vision、reasoning、
  chat/messages/responses 三协议、effort low/medium/high）、`step-router-v1`
  （262144、reasoning、上游自动路由）、stepaudio×3（音频，无消费方未接入）。
- `step-3.7-flash` chat 200：STEP_OK、finish=stop、usage 18/29、reasoning_content 正常。

## 变更

执行脚本：`scripts/ops/add_stepfun_step_plan_channel.py`（key 走 `STEPFUN_KEY` env，dry-run 默认）。

| 项 | 值 |
|---|---|
| 渠道 | ch144 `stepfun-step-plan`（type=1，base `https://api.stepfun.com/step_plan`，不带 /v1） |
| 模型 | `step-3.7-flash`、`step-router-v1` |
| 姿态 | priority 0 / weight 2，auto_ban=1，test_model=step-3.7-flash |
| abilities | 两模型均 `(default,1,0,2)` 读回一致 |
| 定价 | **只读校验未改写**：runbook 2026-07-31 要求 `step-router-v1` ModelRatio=0.5/CompletionRatio=2（库内已在）；`step-3.7-flash` 保留既有 0/3（Cline 池时代残留） |
| DB 快照 | `new-api-before-stepfun-step-plan-20261003-173900.db`（integrity=ok） |
| OMP models.yml | +`step-3.7-flash`（262144/32768，vision，reasoning）、+`step-router-v1`（262144/32768，reasoning）；输出上限 32768 为保守标称 **[未实测]** |
| OMP 链 | `retry.fallbackChains.vision` = `[step-3.7-flash, deepseek-v4-flash-vision, agnes-2.5-flash]`（用户拍板：vision 链首备胎） |

## 验证

- 库内无 stepfun 渠道残留（ch36 已删）；ch110 `yjs-free`（status=2）残留
  `step-3.7-flash` ability（enabled=0, prio=6）——channel/fix 只清死渠道，
  该记录残留**无害**。
- **abilities 缓存竞态实录**：channel/fix 后立即发网关 chat，HTTP 200 但无日志行
  （abilities 缓存未刷新）；数秒后重放即正常归因 ch144。脚本功能段已加
  chat+归因重试环（6×5s）。
- 网关 chat 200（usage 18/188，日志 216932 归因 ch144）；定价只读校验通过。
- OMP E2E：`STEP37_OK`（42.6s 与 LongCat 连跑）。
- step-router-v1：直探网关时逢 429（无 usage/无日志——**疑似**探针连发触发的瞬时
  限流，上游侧还是网关侧未完全归因）；随后 OMP E2E `omp -p --model
  zg-newapi/step-router-v1` → "Hello"，日志 `(217074, ch144, type=2, 48794/212)`
  实锤归因 ch144——router 在役。

## 风险与备忘

1. **ch110 碰撞**：若 `yjs-free` 日后重新启用，其 step-3.7-flash ability（prio=6）
   会压过 ch144（prio=0）抢流量——届时从 ch110 models 中移除该模型或保持禁用。
2. **step-3.7-flash 定价 open item**：官方价见 platform.stepfun.com 定价页，
   确认后更新 ModelRatio/CompletionRatio（现保留 0/3 残留值）。
3. 凭据：本 key 经聊天明文传递，用户裁决**不轮换**（已记录）。
4. Step Plan 消耗用户订阅额度。

## 回滚

禁用 ch144（channels.status=2 + abilities.enabled=0 双表），或还原
`backups/new-api-before-stepfun-step-plan-20261003-173900.db`（会同时回滚 ch145，
注意先后）；models.yml 删两条目、vision 链移除首元素。
