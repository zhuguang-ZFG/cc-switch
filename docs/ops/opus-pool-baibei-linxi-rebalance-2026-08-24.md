# Opus-5 主池：启用百倍+林夕三渠道负载均衡（2026-08-24）

用户指示"启用百倍和林夕，负载均衡，不用考虑渠道亲和"。林夕渠道的启用不是一次
简单的渠道开关——它撞上了三层隔离机制与一个 fork 特有的写路径陷阱。本记录含
完整证据链。

## 林夕被"自动禁用"的根因：Guardian 隔离集（不是 NewAPI auto_ban）

表面证据曾指向"manual operation"，逐层排除后定位到 `guardian.py:1275
enforce_quarantine()`：每个巡检周期对 `AUTO_BAN_RECOVERY_EXCLUSIONS`（`:169`）
中的渠道强制执行 disable。ch9/ch18 在集内，原因为 **linxi 同账号余额耗尽**
（2026-08-10 上游 403 insufficient balance，ch9/ch18 共享同一上游账号）。
ch9 的 NewAPI `auto_ban=0` 只能挡请求失败拉黑，挡不住这条策略级强制。

三处必须同步解封，缺一即回弹：

| 层 | 位置 | 原状态 | 处置 |
|---|---|---|---|
| Guardian 隔离 | `guardian.py` 排除集 + 部署副本 | {2,9,18,…} | 移除 9/18（注释更新：余额恢复，探测双 200） |
| 烟测契约 | `newapi-local-smoke.py:KNOWN_BROKEN_CHANNELS` | 含 9/18，`expected_disabled_violations` 把 status=1 或 weight≠0 视为违规 | 移除 9/18 |
| NewAPI DB | channels + abilities | status=2/w=0 | 见下 |

部署副本与仓库副本有一致性测试（`test_deployed_guardian_exclusions_match_repo_copy`），
两边同步改，不能只改一边。

## 上游根因验证：余额已恢复

解封前管理探测直击：`ch9/claude-opus-5` success=True、`ch18` success=True
——若余额仍为零必返 403。复核当日全池直测（ch3/ch9/ch18）全部 success。

## fork 写路径陷阱：list-PUT 对这两个渠道会静默丢 status/weight

本次踩实的新机构知识：对 ch9/ch18 走"列表端点条目去 status 再 PUT"会把
**status 和 weight 同时写回 0/2**（同一模式在 ch61/ch33 上却正常保留）；
详情端点形状 PUT 一律 400（Invalid parameters，与既有文档一致）。可行组合为：
**list 形态 PUT 元数据 → `POST /api/channel/<id>/status {"status":1}` 启用**。
且每次 PUT 后必须重发 status POST——ch9 曾因脚本中断少发一次而保持禁用。

所有字段级核验以 DB 直读为准（list/详情端点均不回传 key，test_model 等字段
在两种端点间表现不一致）。

## 最终拓扑与行为验证

```
claude-opus-5 池（全启用、auto_ban=0、test_model=claude-opus-5）：
  ch3  baibei-100xlabs         p52/w28
  ch9  linxi-k40               p52/w20
  ch18 linxi-k40-opus5-backup  p50/w8   (备份档)
```

- 9 发 relay 探针全成功，归因分布 {ch18:11, ch3:3, ch9:4}——流量确实分散。
  注意：该 fork 的选路并非"高优先级严格先行"（ch18 低优先级反获最多），
  权重与优先级的实际交互未从样本推断，不做机理断言；持续观测日志即可。
- Guardian 重启（排除集已改）后跨多巡检周期 DB 真值保持：status/weight/
  abilities 三表一致，无回弹。
- 渠道亲和：两文件 grep `affinity|sticky` 零命中——本栈无亲和逻辑，
  "不用考虑渠道亲和"天然满足，无动作。

## 契约加固（防回弹/防塌缩）

- `MIN_ENABLED_CRITICAL_MODELS["claude-opus-5"]=2` 原来只数渠道数，塌缩到
  单渠道仍绿。新增 `PRIMARY_CHANNEL_POSTURES` + `primary_posture_violations`：
  ch3(p52,min w20) / ch9(p52,min w16) / ch18(p50,min w6)，跌破即违规，
  并接入主检查流。
- ch3 自 `DEGRADED_ACCEPTED_DISABLED` 移出（上游 502 已愈，当日 437K quota
  成功计费；该契约条目过期）。

## 工件

- 整库备份：`new-api-before-opus-pool-balance-20260824-184654.db`
  （114MB，integrity ok，含改前 abilities 快照）
- 进程拓扑：watchdog.ps1 + guardian.py 已按原样重启（hub 托管，persist），
  心跳持续；期间撞见一个 r8 SOTA 评审子进程实弹运行（omp -p，PID 14008）。

测试：test_smoke 41/41、test_guardian 178/178、test_omp_routes 39/39。
