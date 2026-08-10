# 本地运维栈深度评审（2026-08-11）

> **修复状态（2026-08-11 当日）**：guardian.py 全部 6 个 P1 已修复并部署（commit `bbfec3be`，镜像 `~/.omp/guardian/guardian.py` 已同步，watchdog 接管重启 03:31:45 上线，心跳正常）。P1-2 随 P1-1 修复自动恢复。配套：新增 8 个针对性测试 + 更新 3 个契约变更测试 + 修复 1 个 08-10 遗留陈旧测试，全套 109 tests OK（仓库与镜像双侧）。存量契约变更：_load_state OSError 由"静默降级 defaults"改为"重试 3 次→拒绝空状态启动"；探针全 incompatible 由"不计退避"改为"计入退避（仍不启用/不禁用）"。P2/P3 未动，排期。

> 方法：3 个评审代理全文通读 + 主代理对全部 P1 人工复核（同 07-27 流程）。
> 范围：`scripts/ops/` 本地栈——guardian.py（2247 行，首次全文评审）、codex-relay.py、newapi-local-smoke.py、watchdog.ps1、system-health-check.py。NewAPI 渠道配置另见亲和一节。
> 复核撤回：codex-relay "上游 socket 泄漏"（误报，`finally: upstream.close()` 在 `return` 时照常执行）、"tool_calls 排序 bug"（误报，int key 字典 sorted 正确）、"信号量未释放"（误报，except 已 release）。

## P1（真实 bug，建议本周修）

| # | 位置 | 问题 | 修法 |
|---|------|------|------|
| 1 | guardian.py:2113-2119 | **主循环 step 6 缺失**：步骤序列 1→2→2.5→3→4→5→5.5→7，`check_channel`(619)/`_record_channel_perf`(861) 生产侧零调用（仅测试调用）→ `channel_perf` 恒空 → `_get_channel_stats` 恒 None → `_auto_adjust_weights`(1070) 全死；慢渠道计数去重(625-644)不可达。单测直调孤儿函数故全绿 | 主循环补 step 6：遍历渠道调 `check_channel` + `_record_channel_perf` |
| 2 | guardian.py:1567/1091/1266 | **降权单向棘轮**：唯一存活降权在 full scan(1567)；唯一权重回升路径 `_auto_adjust_weights`(1091) 已被 #1 杀死；1266 只还原 disabled_channels 内记录 → 降权渠道 10→5→2→1→禁用，永不回升 | 修 #1 后自然恢复；或在恢复循环补 weight_history 还原 |
| 3 | guardian.py:790-804 | **_load_state OSError 静默丢状态**：读失败（AV 占用等）注释写"记录后重试"但直落 `return defaults`；`_save_state` 同一 payload 双写 state.json 与 last-good → 空状态连同备份一起覆盖，disabled/weight_history/冷却全清零 | OSError 分支真正重试（有限次数+退避），失败则拒绝启动而非带空状态运行；last-good 只在主写成功后滚动 |
| 4 | guardian.py:1592-1601 + 508-510 | **cleanup_stale_state 级联删除**：`_request` 失败 → `get_channels()` 返回 `[]` → `channels={}` → 每条 disabled 记录 `cid not in channels` 恒真 → 删记录 + pop weight_history/degraded/joined + 落盘。不可自愈：`_sync_newapi_auto_bans` 只导入 status=3，Guardian 自禁用是 status=2 | `channels = ...` 后加 `if not channels: return`（空列表视为获取失败跳过本轮清理） |
| 5 | guardian.py:1177-1222 | **恢复队列饿死**：探针全 incompatible → 1216 `continue` 跳过 `recovery_failures` 自增 → failures 恒 0 → 退避恒 5min，但 `tested` 已消耗 RECOVERY_BATCH_SIZE=2 配额 → 队首 2 条 incompatible 记录每 5min 重复烧探测，其后渠道永不被探测 | incompatible 分支也自增 failures（或单独 incompatible 退避），且不计入 tested 配额 |
| 6 | guardian.py:974/1139/1369/1548 vs 824 | **排除集绕过**：`AUTO_BAN_RECOVERY_EXCLUSIONS` 只在 `_sync_newapi_auto_bans` 导入时检查；Guardian 自己的 4 条禁用路径与恢复循环都不查 → 排除渠道（20/57/62-65/70/73/74 等）被扫描禁用后又被自动恢复，与 111 行策略冲突 | `_append_disabled` 入口统一查排除集；恢复循环 enable 前再查一次 |

## P2（隐患，排期修）

| # | 位置 | 问题 |
|---|------|------|
| 7 | guardian.py:951-956/1537 vs 940 | `ERROR_DISABLE_KEYWORDS` 裸子串 `"401"/"402"/"quota"` 全文匹配，NewAPI 错误消息常内嵌 request_id/时间戳（如 `20260810123402` 含 402）→ 健康渠道误禁；反向 `_is_transient_rate_limit` 裸 `"429"` 先判定，req id 含 429 会让真·余额耗尽渠道被当瞬态跳过。修法：关键词匹配限定 message 字段/加词边界 |
| 8 | guardian.py:2232 | `CreateMutexW` 用 `Local\` 会话级命名空间 → 任务计划（session 0）与交互式 wscript 可各跑一个 Guardian，`_save_state` 全量覆盖 → lost update + 双份告警。改 `Global\` |
| 9 | guardian.py:2112 + 1356 | step5 恢复循环无总批次上限且用默认 30s 超时 → 群体恢复时单步 150-300s；心跳仅周期开头写（1990-1996）→ watchdog staleSec=180 可能强杀健康 Guardian |
| 10 | guardian.py:1998-2003 | 单一 try 包整个 `_check_cycle`，内部 step 无独立 try → 任一 step 异常吞掉后续全部步骤（含豁免预算的稳定性回滚） |
| 11 | guardian.py:2045-2047 | 先持久化 `newapi_outage_alerted` 再发告警 → Telegram 熔断期间宕机告警永不再发 |
| 12 | codex-relay.py:389-398 | 语义超时（SEMANTIC_TIMEOUT）只在读到 chunk 时检查；上游 stalled read 阻塞期间不触发 → 实际由 urlopen 60s 兜底，语义超时形同虚设。修法：read 改 select/短超时轮询 |
| 13 | newapi-local-smoke.py:341-343/391 | admin 缓存 token 校验遇 403（非 401）→ raise RuntimeError → 整个巡检中断，后续渠道/多 key/冒烟检查全跳过。修法：非 401 也降级为 check FAIL 而非中断 |
| 14 | system-health-check.py（看门狗任务查询） | `Get-ScheduledTask` 返回 null 时未提前退出，插值访问 `$i.LastTaskResult` 崩 NullReference → 该检查项静默缺失。修法：null 判断 + 显式 FAIL |

## P3（小问题）

| # | 位置 | 问题 |
|---|------|------|
| 15 | guardian.py:1115-1116 | finally 中 `history.clear()` 在未采取动作时也清空 20 样本窗口；叠加 #1 修复后可能削弱统计 |
| 16 | guardian.py:1035-1037 | `degrade_channel_weight` 在 update_channel 成功前就地改调用方 channel dict（与 1102-1104 用 copy 不一致） |
| 17 | guardian.py:904-906 | `_error_request_ids` 对 channel_id=None 的日志跳过渠道过滤，告警可能附错其他渠道 request_id |
| 18 | watchdog.ps1:69 | `$data.pid -is [int]` 拒绝浮点 pid；当前 guardian 写入为 int（json.dumps），仅防御性隐患 |
| 19 | guardian.py:233-239 | Telegram `send()` 先睡限流再查熔断，熔断期间白等 |
| 20 | guardian.py:1477-1486 | [推断] 探测携真实 Bearer key 且默认跟随重定向，重定向可泄露 key 到第三方主机 |

## 已核查判定无问题（勿改）

- 错误扫描/全量扫描轮转（933-937、1513-1517）正确；`_save_state` 原子写完整；排除集 isinstance 守卫（822）；models.yml 解析与 `_probe_endpoint` "任何 HTTP 响应算存活"语义对其用途合理（5xx 已排除）
- `_SECRETS` 空降级：1911-1919 有必填校验并 raise，非静默
- codex-relay `process_request` 信号量异常路径已 release；tool_calls int-key 排序正确
- step9 `channels` NameError 不可达（`_budget_left` 单调，step3 跳过则 step9 必跳过）

## 附：渠道亲和（claude trace，2026-08-11 已配）

背景：prompt caching 实测生效（sotamodel 文档），但 `channel_affinity_setting.rules` 原有 6 条规则（codex/glm/grok/deepseek/longcat/qwen）**无 claude**——claude 流量按权重随机落池，会话缓存无法粘住。

已新增第 7 条规则（PUT /api/option/ 单条格式，已落库复核）：

| 项 | 值 |
|----|----|
| name | `claude trace` |
| model_regex | `^(?:claude-.*\|zg-claude-.*)$` |
| path_regex | `/v1/messages`, `/v1/chat/completions` |
| key_sources | gjson `metadata.user_id` → gjson `prompt_cache_key` → header `User-Agent`（逐级回退） |
| ttl_seconds | 600（覆盖 Anthropic 5 分钟缓存窗；其余规则 300） |
| include_model_name / group / rule | true / true / true |

**验证**（NewAPI stdout 日志 admin_info.channel_affinity）：探针 `metadata.user_id=affinity-probe-20260811` ×7 → 全部钉 ch76；生产 Claude Code 流量按会话分流（fp `cfdfb57b`→ch78、`669d26a8`→ch76、`8d76904b`→ch78），每会话稳定单渠道。

**遗留缺口**：其余 6 条规则的 model_regex 不含 `zg-` 前缀（如 `^gpt-.*$` 不匹配 `zg-gpt-5.6-sol`）——OMP 走 zg- 模型名的 gpt/deepseek 流量实际未命中亲和。claude 规则已含 `zg-claude-.*`，其余待按同法补齐（需先确认亲和匹配发生在 model_mapping 之前）。

回滚：`PUT /api/option/` 把 `channel_affinity_setting.rules` 写回不含 claude trace 的 6 条版本。
