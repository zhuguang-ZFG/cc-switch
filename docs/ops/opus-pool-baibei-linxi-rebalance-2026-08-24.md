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

## 附:claude trace 渠道亲和单独禁用(2026-08-24)

该 fork 有全局渠道亲和功能(`options.channel_affinity_setting.*`),7 条按模型
家族的规则;UI 渠道页的"渠道亲和性: claude trace"只是显示该渠道参与的规则,
并非渠道自有配置。claude trace 规则匹配 `claude-*` + `/v1/messages`+
`/v1/chat/completions`,粘性键 metadata.user_id/prompt_cache_key/User-Agent,
TTL 60s——这正是早前探针分布粘连 ch18 的原因(第一发落点+TTL 内全部粘连)。

用户指示"林夕百倍不用渠道亲和"。规则结构体无渠道级排除字段(二进制确认
`channel_ids` 标签属其他结构),而当前启用的 claude 渠道恰好就是 3/9/18,
故最小可逆动作 = 单独将该规则 `enabled=false`(其余 6 条家族规则不动)。
回滚快照 `options-affinity-before-20260824-192323.json`(原值 enabled=true)。

行为验证:禁用后 9 发 relay 探针分布 {ch9:5, ch18:4, ch3:2},全部成功,
对比禁用前的 TTL 粘连形态 {ch18:11, ch3:3, ch9:4} 明显散开。代价说明:
Claude 提示词缓存亲和失效,长会话跨渠道时 cacheRead 命中率会下降。

注:SOTA 评审曾建议"重写 Guardian 恢复"——误判。`check_and_enable_recovered_channels`
每周期正常重试 ch102/103(当日 18:52/19:15 有记录),它们持续禁用是因根因未消
(死 key/零余额),属正确行为;恢复手段是换 key/充值,不动 Guardian 代码。

## 附 2:其余 opus 渠道转 p40 备份档(2026-08-24 晚)

用户指示"其他渠道的 opus 做备用"。候选 9 渠道逐一用**网关自带**
`GET /api/channel/test/<id>?model=claude-opus-5`(真实 Go 客户端)实测:
直连 python urllib 探活全部被 Cloudflare 1010 指纹封锁(假 403),只有网关
自测可信。结果:57/75/86/94/95/97/98 通过,72(anyrouter 上游 429)与
99(tab3 余额 ＄0.11<＄0.80 预扣费)排除。

最终拓扑:
```
主池:ch3 p52/w28, ch9 p52/w20, ch18 p50/w8
备份 p40:ch86 w13(agentrouter, TTFT~25s 垫底), ch94/95 w8(justwoker),
         ch57/75/97/98 w5(gorouter/tabitoken)
```

三个新机构知识:
1. **失败 PUT 有破坏性副作用**:对 type=14 等渠道,`Invalid parameters` 的
   PUT 也会把 status 写回 2。list 端点分页取到的对象形状才是 PUT 可接受的
   (search/detail 端点形状均被拒;detail 还掩码 key/channel_info)。
2. **Guardian `enforce_quarantine` 是主动反转器**:对排除集内渠道每周期
   强制 status=2+weight=0。本次启用后 4 渠道被 19:59 周期反转——文件改完
   必须重启 guardian 进程才生效(已重启,pid 12756)。
3. 渠道启用 ≠ ability 启用:PUT 须在渠道已启用状态下重放一次,ability 行
   才同步 enabled/weight(本轮用"PUT→status POST→DB 核验"循环收敛)。

契约:`KNOWN_BROKEN`/`AUTO_BAN_RECOVERY_EXCLUSIONS` 移除 57/75/97/98(99 保留);
新增 `BACKUP_CHANNEL_POSTURES` 门禁——备份档启用时 priority>40 或超重即违规,
禁用抖动容忍(交 Guardian 恢复)。每渠道改动前均已存 `channel-<id>-before-backup-*.json`。

测试：test_smoke 41/41、test_guardian 178/178、test_omp_routes 39/39。
