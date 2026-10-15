# 全渠道体检 + 状态更新（2026-10-15 晚）

用户令「渠道都检查更新下」。范围：全库 102 渠道。方法：零成本遥感
（状态/72h 流量/Guardian 归因/任务姿态）+ 51 渠道真探针（33 enabled 全量
+ 18 条 disabled 里近期有流量或带恢复线索的）。

## 探针结果（admin test，失败自动复测一次）

### enabled 33 条

| 类别 | 渠道 |
|---|---|
| LIVE（24） | ch15/33/61/66/67/77/88/118/124/133/138/173/178/180/185/187/188/189/190/191/199/200/202/218 |
| DEAD/ERR（9） | **ch127**（402 `Budget pool quota has been exhausted`，已 park）、**ch186**（403 `订阅额度不足: subscription quota insufficient, bucket=basic`，已 park）、**ch224**（429 `you have no left credit for step plan`，已 park）、ch3（探针 90s 超时，Guardian 已降权 10→5）、ch72（429 Service Unavailable，任何router 窗口型）、ch171（429 rate limiter/insufficient balance）、ch181（本地桥 do request failed，Guardian 已降权）、ch182（do request failed ×2）、ch204（本地桥 do request failed，Guardian 已降权） |

### disabled 18 条（近期有流量或恢复线索）

| 类别 | 渠道 |
|---|---|
| **LIVE（1）→ 回开** | **ch208 happycoding**（glm-5.3 探针 3/3 200；10-14 因钱包负值禁用，现已回血） |
| DEAD（17） | ch9（502 accounts exhausted）、ch18（403 balance）、ch57（502）、ch69（403 账号异常）、ch94/95（503 no available channel）、ch109（429 额度等待恢复）、ch139（429 quota exhausted，已定案不回开）、ch175（403 balance）、ch183（401 invalid key）、ch207（429 余额不足）、ch209/ch217/ch219/ch222（401 invalid token/key）、ch220（403 负额）、ch221（401 invalid key） |

## 已执行的更新

1. **ch208 回开**（`POST /status {"status":1}`，readback status=1）；
   abilities 5 行（glm-5.3/glm-5.3-flash/kimi-k3/minimax-m3/deepseek-v4.1-flash）
   实测 enabled=1 → 恢复 glm-5.3（OMP 侧 quarantine 条目已撤）、kimi-k3、
   minimax-m3，并给 glm-5.3-flash / deepseek-v4.1-flash 各加一腿。
2. **park 三条硬证据死腿**（status=2 + auto_ban=1，房式 park：
   `park_zombie_channels_20261007.py` 同款；abilities 随禁翻 0 已核）：
   ch127（402 预算池耗尽，与 10-07 park 原因一致）、ch186（订阅额度不足）、
   ch224（step-plan 额度尽）。
3. **门禁 quarantine 表同步**：撤 `glm-5.3`（已复活）；增 `qwen3.8-flash`
   （ch186 为唯一腿）；`step-*` 四条补记 ch224。
4. 备份：`new-api-before-chstatus-20261015-180913/180914/180945.db`（三份，
   各自写前快照）。

## 门禁（改动后实测）

- `verify_omp_newapi_alignment` → **CLEAN**：68 ids / 0 orphan / 24 quarantined。
- `test_ops_gates` 11/11（含 QUARANTINE 重复键守卫）。
- `newapi-local-smoke`（18:11 轮）→ FAIL 仍为基线 8 类，无新增；enabled
  33→31（-3 park +1 回开）；`intentional channel disables` OK（park 走
  auto_ban=1 机器归因，不计 unexpected）。

## 遗留

1. **enabled 死腿未处置（5）**：ch3（超时；`ch3 100xlabs Reprobe` 计划任务
   处于 **Disabled**，自动回捞失效）、ch72（任何router 窗口型，门禁
   `test_unhealthy_anyrouter_is_not_an_automatic_fallback` 即为此设计）、
   ch171/ch182（瞬态存疑，保留观察）、ch181/ch204（本地桥，服务重启即回）。
2. **三处看护/恢复装甲被停用**（需裁决是否恢复）：
   `NewAPI ch3 100xlabs Reprobe`（15min，Disabled）、
   `AnyRouter Window Canary`（5min，Disabled）、
   `NewAPI Guardian Watchdog`（登录触发，Disabled）。
3. **ModelRatio 6 名**仍待用户给值（qwen3.8-flash 现随 ch186 park 自然止血，
   但名仍在册）。
4. **渠道模型目录对账未做**：本轮只做状态体检；逐渠道
   `/v1/models` 目录刷新（如 ch173 裁剪那种）留作后续，需要时按渠道点单。
5. Guardian 侧：`disabled_channels` 51 条，多数 `recovery_exhausted=True`
   （它已停止探测）——本表为其超集，状态以本 runbook 为准。

## 回滚

```bash
# 逐渠道：enable/park 反向执行
# 整库：cp ~/.new-api-local/backups/new-api-before-chstatus-20261015-180945.db <db>（停机时）
# 门禁文案：git revert <本 commit>
```
