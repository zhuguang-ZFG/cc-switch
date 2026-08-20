# Guardian / Smoke 加固（2026-08-20）

当天四处实际踩坑的固化，全部落在 `scripts/ops/` 仓内副本 + 部署副本。

## 1. 余额耗尽特征识别（guardian.py）

背景：tabitoken ch97/99 余额低于预扣门槛后上游回 403 `预扣费额度失败`，
sotamodel ch93 免费日额度耗尽回 429 `daily_free_credits_exhausted`——两类报文
都不含旧 `ERROR_DISABLE_KEYWORDS` 任何关键词，错误扫描跳过，渠道持续接客，
OMP 侧表现为反复故障路由。

修复：词表新增 `预扣费额度失败`、`daily_free_credits_exhausted`（文本子串匹配，
与 `quota`/`余额不足` 同语义）。命中后走既有 error_scan 自动禁用 + Telegram
告警 + 有界恢复队列（充值后恢复探测自动回捞，无需人工）。测试：
test_guardian.py KeywordBoundaryTests 新增 2 条（含"不得被 429 瞬态限流跳过"
反向断言）。

## 2. 部署副本漂移门禁（test_smoke.py）

背景：仓库 `scripts/ops/guardian.py` 与部署副本 `~/.omp/guardian/guardian.py`
漂移——当天部署副本排除集漏更 75/98，tombstone 渠道面临被恢复队列回捞的风险。

修复：`test_deployed_guardian_exclusions_match_repo_copy` 逐字解析两份文件的
`AUTO_BAN_RECOVERY_EXCLUSIONS` 并断言相等；部署文件缺失（CI）时 skip。
**改动排除集后的固定动作**：`cp scripts/ops/guardian.py ~/.omp/guardian/` +
`apply-secrets-restart.ps1` 重启。

## 3. opus 池容量阈值（newapi-local-smoke.py）

背景：`MIN_ENABLED_CRITICAL_MODELS` 的 `claude-opus-5: 1` 在池塌缩到 ch86
单渠道时仍是绿的（tabitoken 停产后即此状态，ch86 大 prompt 挂起无人报警）。

修复：`claude-opus-5` / `claude-opus-4-8` 均提到 min=2（当前容量各 3：
justwoker ch94/95 p50/w8 + ch86 p40/w13 兜底）。塌缩到单渠道即 FAIL。

## 4. 零输出计费侦测（newapi-local-smoke.py）

背景：ch86 一个 67.9k tokens 请求上游 120s 零输出、client_gone，仍计费 ¥0.5。
这类"上游挂死"此前无任何告警面。

修复：`zero_output_billing_violations()` 直读 logs 表（只读）：6h 窗口内
consume 日志 `completion_tokens=0 且 use_time>=120s 且 quota>0`，按渠道聚合，
单渠道 ≥2 条才报（用户手动取消的偶发单条不报）。接入 main 为
`zero-output billed streams` 检查。当日实测窗口内 ch86 仅 1 条，未误报。

## 验证

- test_guardian 167 项、test_smoke 41 项、test_omp_routes 37 项全绿；
- Guardian 已部署重启（pid 28160）；
- 零输出检查对真实库运行：none（未误报）。
