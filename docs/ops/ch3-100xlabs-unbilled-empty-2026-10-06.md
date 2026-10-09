# ch3(100xlabs) 未计费空回 — 上游池垮塌 fail-closed + 复探回捞 — 2026-10-06

## 事件

LiMa Gallery Bot 23:42/23:47 两发「未计费空回超标」：ch3 `claude-opus-5-5`
6h 空回率 51.4%（18/35），阈值 20%（guardian.py `_step_unbilled_empty_rounds`，
口径与 2026-09-14 ch126 runbook 相同：type=2 且 prompt=0/completion=0/quota=0）。

## 取证（全部只读）

日志侧（new-api.db）：
- 空回是**突发**非稳态：20:00 时段 2/14，21:00 时段 16/21。
- 有 2 发**计费空回**（prompt=56713, completion=0, quota=2,126,738 各）——
  监控口径外的更坏形态，大上下文被静默吞且照扣 quota。
- 同期 error 日志 0 条——上游 200 空回，NewAPI 无错误可挂重试。
- 流量全部来自本机 `local-windows-clients` token。

直探侧（绕过 NewAPI，逐 key 打 `https://sub.100xlabs.space/v1/messages`，
流式、max_tokens=16）：

| key | opus-5-5 ×3 | fable-5.1 |
|---|---|---|
| k1 | 1 成功(63s)、2 空200 | 502 |
| k2 | 3× 空200 | 502 |
| k3 | 2× 502、1× 空200 | — |
| k4 | 2× 502、1× 空200 | — |
| k5 | 2× 空200、1× 502 | — |
| k6 | 3× 502 | — |

合计 18 发 1 成功（5.6%）、11 空 200、6× 502，延迟 18–63s。

**假设裁决**：H1（个别 key 失效，51.4%≈3/6 key）被否——6/6 key 全 degraded；
H2 成立——**100xlabs 公益池整体垮塌**（与 10-05 登记条目注释的"429/502 抖动常态"
一致，本次是程度升级）。

## 处置面约束

- ch3 是 `claude-opus-5-5`、`claude-fable-5-1`、`claude-fable-5.1` 三模型的
  **唯一腿**（abilities 各仅 1 行，priority 54 独占）。
- 降权无用：独优先级档权重不改变流量分布（PINNED_CHANNEL_WEIGHTS 注释同款原理）。
- 09-14 runbook 既定两手段（查上游/降权）均穷尽 → 用户拍板 fail-closed。

## 变更

1. **禁用 ch3**（用户授权，fail-closed）：
   `POST /api/channel/3/status {"status":2}`（专用 status API，无需 key 水合）。
   验证：API/DB status=2，abilities 3 行 enabled=0 同步；fail-closed 实证——
   relay 请求秒回 `HTTP 503 model_not_found "No available channel for model
   claude-opus-5-5"`（0.0s），不再是静默空回。
2. **回滚件**：`~/.new-api-local/backups/ch3-100xlabs-disable-pre-20261006-002311.json`
   （渠道行 + abilities，key 仅 sha256 指纹；回捞 = status API 置 1）。
3. **复探哨兵**：`scripts/ops/ch3-100xlabs-reprobe.py`（部署副本
   `~/.omp/guardian/ch3-100xlabs-reprobe.py`，sha256 双侧一致，改后重部署）：
   - 计划任务 `NewAPI ch3 100xlabs Reprobe`，每 15min，
     scoop python313 pythonw（对齐 AnyRouter Window Canary 模式）。
   - 每轮：status==1 直接退（幂等）；status∈(2,3) 才探。
   - 轮换抽 2 key × 2 发流式探针；**4/4 全部"200+正文+usage+收尾"才回捞**
     （垮塌期单发假阳性 ~6%，4/4 误捞概率 ~1e-5）。
   - 回捞后**双重复核**：GET status==1 **+ abilities 三行 enabled==1**
     （禁用会把 abilities 翻 0，status=1 但无腿=半开；检出后先
     `/api/channel/fix` 补救，仍坏则 🚨 告警），全过才发 ✅。
   - 单实例锁（10min 陈旧接管）、日志 512KB 截断、key 只读 DB 不落盘；
     DB 连接显式 close（`with sqlite3.connect` 只管事务不关连接，
     Windows 下残留文件锁——测试期实测踩中）。
   - 与 guardian 无抢跑：`_sync_newapi_auto_bans` 只导入
     `status==3 且 auto_ban∈{1,true}`（guardian.py:1549-1555，代码实证），
     本次 status=2 手工禁用进不了 guardian 恢复队列；guardian state 无 ch3 记录。
   - 局限：无其他禁用原因感知——若日后因别的原因禁用 ch3 且不想自动回捞，
     删除该计划任务即可。
4. **测试**：`scripts/ops/test_ch3_100xlabs_reprobe.py` 19 用例全绿
   （空 200 判 fail / 缺 usage 判 fail / SSE error 判 fail / 3/4 不回捞 /
   status∈(2,3) 在回捞范围 / key 轮换全覆盖 / 锁新鲜阻塞+陈旧接管 /
   abilities 全绿通过 / 单行 disabled 判半开并点名模型 / 无 abilities 行 fail-closed）。

## 验证

- 手动实跑一次（池仍垮塌中）：113s 退出 rc=0，探针 1/4（1 发假阳性真内容 +
  3 发 SSE error event）→ 门禁正确拒绝回捞，ch3 保持 status=2，
  state/log 落盘正常（UTF-8）。
- 计划任务调度路径实证：自然 tick（00:44）与强制 `/run`（00:45:25）均在
  pythonw 下跑完、schtasks 上次结果=0；三轮探针 1/4、2/4、0/4 全部 <4/4
  门禁——池在抖动期（部分探针偶发成功）仍未误捞，ch3 保持禁用。
- Telegram 处置通知已发（msg_id 4693）。

## 后续

- 池恢复后哨兵自动回捞并发 ✅ 告警；无需人工值守。
- fable-5-1/5.1 同为 ch3 独腿、同池同病，随 ch3 一并回捞。
- 若池长期（数周）不恢复：考虑下架 OMP models.yml 的 `claude-opus-5-5` 条目
  （commit c8b0fbf）并清 abilities 残行，属另案决策。
- 计费空回（prompt>0/completion=0/quota>0）监控口径未覆盖，本次纯人工发现；
  如需覆盖应另立监控项（不动既有 20% 阈值口径）。
