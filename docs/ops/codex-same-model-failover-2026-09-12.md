# Codex 同模型无缝故障转移 · 2026-09-12

## 事件

用户 codex CLI（provider `any` → `http://127.0.0.1:3002/v1/responses`，模型 `gpt-5.6-sol`）反复报：

```
Unexpected status 503 Service Unavailable: Codex rolling spend limit exceeded.
Used $15.05 in the last 3 hours, limit is $15.00. Please retry after 3809 seconds.
（traceid: 1a75362b-2a6d-46b4-83f3-b5683b1df3b2）
```

随后用户补报 **`gpt-6-astra` 也吃同一 503**（traceid `6b68050b-…`，retry 2653s）→ 证明 **SharedChat 的 $15/3h 滚动限额按 key 计，sol 与 astra 共享 ch128 同一窗口，换模型绕不开**。

## 根因链

1. ch128 `sharedchat-codex-astra`（p60，sol+astra，上游 `https://new.sharedchat.cc/codex`）上游返回 403 滚动限额（公益站按 key 挤窗，自愈型）。
2. affinity 规则 `codex cli trace` 的 **`skip_retry_on_failure: true`**（2026-09-12 凌晨 anti-brick 设计）= 已钉扎会话失败后**不重试**，SharedChat 错误直接冒泡。
3. 未钉扎请求（合成探针实证）本来就正常重试：ch128 403 → ch127 402 → 冒最后一个错（P0/P1 探针均见 ch127 的 402 Budget pool 文案）。用户痛点只在钉扎路径。
4. ch127 `agentrouter-codex-gpt`（p40，**模型集 = gpt-6-astra,gpt-5.6-sol**）上游 402 `Budget pool quota has been exhausted` —— agentrouter 侧 GPT 预算池枯竭，**用户侧动作**（面板充值/换池）。

## 变更（用户指令 supersedes 同日 anti-brick pin）

- **admin API 单选 PUT** `channel_affinity_setting.rules`：codex 规则 `skip_retry_on_failure` **true → false**。运行态生效，**未清 affinity cache**（有活跃会话，纪律 §9.1），未重启服务。
- 备份：`C:\Users\zhugu\.new-api-local\backups\channel-affinity-20260912-034402.json`（6608B）。
- 回读验证 false；`channel_affinity_setting.enabled` 仍 true。
- `repair_codex_sharedchat.py` 与 `test_repair_codex_sharedchat.py` 同步改为**强制/断言 false**（否则手工重跑会把钉扎翻回去）；pytest 1 passed。
- 冒烟：`newapi-local-smoke.py` 与 affinity 相关检查全绿（affinity aliases OK、Sol primary posture OK、intentional disables OK）。渠道状态类 7 FAIL 为**与本次无关的漂移**（见下）。

## 翻转后的预期行为

- 新会话：ch128 限额窗口内 → 直接落 ch127（池活时）；astra 另有 ch126（p40）rung。
- 已钉扎会话：ch128 失败 → 重试 ch127 —— 池活且无外来 reasoning → 换绑成功；有 SharedChat 历史 reasoning → agentrouter 400「item not found」→ 亲和仍钉 ch128（switch_on_success 只在成功时换）→ **窗口重置后自动恢复，不会永久砖**；急救路径 `codex-resume-scrub.py`。
- 窗口重置（错误里 retry-after 秒数为准）后所有会话回到 ch128 正常服务。

## 渠道面实录（03:45 直探）

| 渠道 | 状态 | 直探结果 |
|---|---|---|
| ch128 sharedchat-codex-astra | status=1 p60 | 403 rolling spend limit（窗口自愈，~2653s） |
| ch127 agentrouter-codex-gpt | status=1 p40 | **402 Budget pool 枯竭（用户侧充值）** |
| ch126 any-gpt-6-astra | status=1 p40 | 400 `invalid codex request` —— 已知 **false-negative**（09-10 定案：admin test/合成探针不算数，须真 codex live fire；09-11 记 deployment dead，待复验） |
| ch94/95 justwoker（8790 桥） | disabled | 500 `convert_request_failed`（桥只有 chat 面，无 responses 面） |
| ch83 muyuan | disabled | 503 上游池空 |
| ch87 ooioo | disabled | 403 余额 $0.000006（需登录充值） |
| ch82 7758 | disabled | 401 Invalid token（key 死，09-11 同） |
| ch91 jianzhile | disabled | key 列含**两把 key 以 `\n` 相连**（探针头会炸），上游池空（09-11） |
| ch92 zzzcoding | status=2 | 405 nginx（`/v1/responses` 线路不对，需按 zzz runbook 重接） |

## 结论：现在还差什么

翻转只把「顶上」机制接通；**当前时点 sol/astra 没有任何活渠道**：

1. **agentrouter GPT 预算池充值/换池**（用户侧，agentrouter.org 面板）——充值后 sol+astra 同模型自动顶上即闭环。
2. （可选）any 的 astra 用**真 codex** 复验（合成探针恒 false-negative）；活了则 astra 多一档 rung。
3. （可选）ooioo 登录充值、7758 换 key、zzz 重接线路——多备几档。

## 遗留漂移（与本次无关，待单独排查）

smoke 7 FAIL：`channels` unexpected_disabled（3:baibei-100xlabs、9:linxi-k40、18:linxi-k40-opus5-backup）、`ai.168661` ch78 missing、primary opus pool posture、pool capacity claude-opus-5/4-8、critical ability posture（45/92 的 sol abilities missing）、sensenova-6.7-flash-lite 404。guardian.log 无 ch3/9/18 ban 记录，归因未定。

## 附：TUI 静默退出复盘（当日 12:50–13:00 午后追加）

用户主诉 codex TUI「经常自己退出」（无声消失回 shell）。证据源：`~/.codex/logs_2.sqlite`（logs 表，按 process_uuid 分组）、`thread_history_1.sqlite`（thread_turns，**时间戳为秒级 epoch，勿按 µs/ms 解析**）、rollout jsonl。Application/System 事件日志、WER ReportArchive、CrashDumps 均 0 记录 → 非 OS 级崩溃。codex 0.154.0（npm 最新，上游未修）。

### 死亡分型（按进程末尾日志形态）

| 类 | 进程 | 末尾形态 | 归因 |
|---|---|---|---|
| (a) 干净 Shutdown（对照基线） | 21260（探针） | `no subscribers → Shutdown op → Shutting down Codex instance → Agent loop exited → thread/closed` | 正常退出路径 |
| (b) 错误边界无声死 | 4672、3300、26328、1924* | turn error → `error` 事件 → `turn/completed` → 戛然而止，**无关闭序列** | 与上游错误风暴强关联；疑似 #36527 app-server 队列/backpressure 消费端崩溃 |
| (c) code-mode exec 流中静默死 | 11536、15192 | outputDelta 流式中断、无 error、custom_tool_call 悬空 | #35341 签名（0.145.0 同 Win build 26200） |
| (d) 末秒 turn 级 ERROR | 11204 | 同秒 `Failed to run pre-sampling compact` | 单例，未定 |

\* 1924 仅 63s 探活会话，正常应有 (a) 式关闭链而其无，样本弱。其余 sub-2min 进程多为 headless 探针正常退。20768（12:55 起）存活中，不计入。

### 与限流风暴的关联（相关非充分）

- 当日 5 次异常死亡**全部**落在其进程最后一次 5/5 重试耗尽后 11s～174s 内（4672 11s / 11204 80s / 15192 81s / 26328 149s / 11536 174s）；4672 的 `turn/completed` 紧跟 SharedChat $15/3h 503。按 5/5 事件约每 20min 一次的密度，5/5 全中窗口远非巧合。
- 但 5/5 耗尽本身非充分条件：多例耗尽后继续存活 24min～2.6h（6095s/5843s/9184s）；15192 在 5/5 后 81s 仍跑完一个 10.2s 脚本工具调用、死于下一个 exec 流。
- **结论**：上游错误风暴（eastus2 429 + SharedChat 503 + agentrouter 402 叠加期）是触发面之一；致死机制在消费端（code-mode 运行时 / app-server 队列），对应上游 openai/codex #35341、#36527、#14709。限流问题按正文渠道面治理，与本节退出问题并行、不互斥。

### thread_turns 分布（近端上游链状态佐证）

completed 128 / failed 130 / inProgress 23 / interrupted 7 —— 失败率近半，与正文「sol/astra 无活渠道」一致。

### 缓解（本地可做）

1. 死后 `codex resume` 续线程（rollout + thread_history 数据保全，线程可续）。
2. 会话内明确要求用直接 shell 工具、不走 `functions.exec`（#35341 社区 workaround）。
3. 可选对照试验：暂去 `~/.codex/config.toml` `[windows] sandbox` 行观察一轮（未代改，留用户决定）。

## 纪律重申

- 探活先行；改 affinity 不清 cache（活跃会话在场）；key 只读不打印。
- 回滚路径：把备份 JSON 的 rules 经同一 admin PUT 写回（或手工把 codex 规则 skip_retry_on_failure 改回 true——但那是被 supersede 的旧策略）。

## 附 2：限额类禁用的全流程自动化（当日 13:00–13:40 追加）

### 背景：限额禁用 ≠ 渠道死亡，但恢复引擎当成了死亡

- **ch128 sharedchat-codex-astra**（11:13 事件）：上游报「本时段全站额度已用完，请在 今天 12:00 后再试」——**报文自带重置点**，自愈型限额。Guardian 关键词命中 "quota" → 通用禁用 → 指数退避。12:00 上游重置；12:25 首次探测失败一次 → 60min 封顶退避 → 下次 13:25；13:10 人工启用，仅抢回 ~15 分钟。
- **agentrouter ch127 预算池**：402 "Budget pool quota has been exhausted" = **定时放量**（预算池按计划 refill），402 属自愈型；重试风暴期间 Guardian 的窗口预算分类正确**保持启用**（is_window_budget_exhausted），无需禁用；报文无重置时间，不宜墓碑。
- **kimi ch33 周额度 / aliyun ch31 token-plan 周额度**：同类 quota 原因，长退避让额度重置后的回池最多延迟 1 小时。

### 修复（2026-09-12 13:22 部署 `~/.omp/guardian/guardian.py`，仓库镜像同步）

1. **自愈型限额报文的显式重置点解析**（`_self_healing_reset_iso` + `SELF_HEALING_QUOTA_MARKERS`）：「请在 今天 HH:MM 后再试」→ 墓碑到该时刻（已过则立即探测）；「rolling spend limit … Please retry after N seconds」→ 墓碑到 now+N+60s；无时间提示（agentrouter 预算池）与瞬态 429 retry-after **不墓碑**。
2. **墓碑到点清陈旧退避**：`daily_cap_until` 过期即清 `recovery_failures`/`last_recovery_attempt`，按新冷却周期起步（修 ch128「重置后仍背 60min 旧债」）；重复限额事件原地刷新墓碑时同样清计数。
3. **quota/额度类退避封顶 15min**（`RECOVERY_BACKOFF_MAX_QUOTA`，原 60min）：定时放量/额度重置类上游回池快；非配额原因仍 60min。

### 验证

- `test_guardian.py` 184 用例全绿（新增 6：报文重置点解析固定时钟双相位、retry-after 解析、无提示/瞬态负例、墓碑到点清退避、quota 15min 封顶、非配额仍 60min——后三对旧实现必失败）。
- 线上重启（PID 28484，13:22:03）后 state.json 证实 ch31/ch75/ch97/ch98（quota/额度类）立即恢复探测（旧代码需等到 13:49–13:53）；ch33/ch128 按每周期 2 个的批处理队列陆续轮到。
- 回滚：`guardian.py.bak-20260912-recovery-auto`。

### 充值指导修订

- SharedChat 全站时段额度 / agentrouter 预算池（定时放量）/ kimi·aliyun 周额度：**无需充值干预**，禁用后由恢复引擎按报文重置点（或 ≤15min 退避）自动回池；人工启用仅在想提前回池时才需要。
- 仍需人工干预：**余额耗尽类**（预扣费额度失败、credit insufficient balance）与结构故障（405 线路错、key 失效）。

### 遗留：仓库副本漂移

`scripts/ops/guardian.py`（repo）落后线上 ~119 行（file-tail 错误侧观测、预算感知扫描偏移等 09-12 凌晨特性 live-only）；本次三处修复已双端同步（线上先行重启、仓库镜像）。待单独开任务做一次全量 diff 对齐后再清理旧 `.bak`。

## 附 3：busy ≠ 死——上游容量信号的探测分类（当日 13:40–13:50 追加）

### 现场因果链（13:26–13:40）

1. ch128 于 13:22 恢复探测未过稳定闸（上游当时 1/3 抖动）→ **闸门正确再禁**（13:23:40 "auto-enabled before stable; disabled again"）；用户侧 503 实为路由沿梯级走到 ch127 后冒泡的最后错误（"Budget pool"），ch128 真实错误 = 503 `Only one Codex conversation can run at a time`（公益站**单 Codex 会话并发**硬上限）+ `Codex model price is temporarily unavailable`（价格表抖动）。
2. 13:29:31 人工启用（当时 3/3 管理探测通过）；13:33–13:35 用户侧真实流量恢复（流式 chunk received=78–143）。
3. 13:38:51 引擎恢复探测时**用户会话正占住单会话槽** → 3 探全报 "Only one Codex conversation" → 稳定闸门把"忙"判成"不稳定" → **把唯一活渠道再禁**。这是误分类：忙应答是上游**带内协议应答**，恰证明链路与协议存活。

### 修复（13:44 双端同步，回滚 `guardian.py.bak-20260912-busy-probe`）

- `UPSTREAM_BUSY_MARKERS` + `_is_probe_busy()`：`only one codex conversation` / `model price is temporarily unavailable` 归类为容量信号。
- 恢复判定放宽：`stable_count + probe_busy_count == RECOVERY_TEST_COUNT` 即视为上游存活 → 走恢复路径（启用/入池/清记录），日志带 `N busy-alive`；**busy 与硬失败混合时仍走原路径**（硬失败优先，不掩盖滚动消费 403 等）。
- 两条扫描（error/full）的软失败豁免分支加入 busy：忙渠道不累积软失败，不触发 disable_slow_channel。

### 验证与当前池状态（13:44–13:47）

- `test_guardian.py` **186 用例全绿**（新增 2：全 busy 恢复且零禁用调用、busy+硬失败混合仍再禁——后者对旧实现行为一致，前者是防回归）。
- 引擎重启后三连探测 3/3（<1s）→ ch128 重新启用（status=1, weight=5）；下一恢复窗口自动清算 state 记录（busy 亦算存活，不再自禁）。
- 池状态：ch128 启用（唯一健康 rung，单会话并发=上游硬约束）；ch127 agentrouter 预算池等定时放量（放量后自动成为第二 rung）；ch126 判"结构性死渠道"（404 "当前 API 不支持所选模型"）——**此结论 14:15 被用户推翻，见续篇勘误**。
- 遗留后手：zzz(ch92) 405 重接、justwoker 8790 桥加 responses 面、仓库↔线上全量 diff 对齐。

### 续篇：stability 回滚 flap + ch126 复活调查（14:04–14:12 追加）

**13:55–13:57 flap 因果链**：13:55:02 busy≠死修复生效，ch128 恢复入池并注册 stability 窗口 → 13:55:36 稳定性监控探到 403「请使用最新版的codex客户端或codex cli调用」被 `_is_probe_incompatible` 兜底条款**碰巧跳过**（应答体恰好含 `invalid_request_error` 且 traceid `20dfdc97-a2ab-4404-…` 含 "404" 子串）→ 13:56:27 同文案但应答换形状（无该字段），未命中兜底 → 记 stability_fails=1 → 13:57:34 全站额度 403 记 fails=2 → **stability_rollback 再禁**（无墓碑字段）。三个独立缺陷叠加：应答形状抖动致分类靠运气；忙/自愈额度未纳入 skip；回滚记录不带 `daily_cap_until`。

**ch126 复活者定性**：GIN 日志全天 `/api/channel/126` 仅 12×GET（面板轮询）+ 2×POST/status——13:29:47 为 Guardian 自身禁用；**13:42:43（11.5ms）为唯一启用事件**，来自 127.0.0.1 管理端 API。旧引擎（PID 28484）已排除：`_sync_newapi_auto_bans` 只导入 status==3（全文核过），恢复循环只对 state 记录内的渠道动作（13:29/13:39 state 快照均无 126 记录），13:41–13:44 引擎日志无任何 enable 动作；file-tail 观察器只读。结论：**面板人工点击启用**（当时面板正开着，GET /api/channel/?p=4 于 14:01 仍在轮询）。已重新禁用（14:05 readback status=2）。⚠️ NewAPI `AutomaticEnableChannelEnabled=true` 只碰 status==3 自动禁渠道，与 status=2 人工禁无关，未动。

**stability 监控补丁（14:09 双端同步部署，引擎 PID 5392）**：skip 分支扩为 `_is_probe_incompatible or _is_probe_busy or _daily_cap_reset_iso is not None`（探针拒收 / 上游忙 / 自愈型额度提示均不累计回滚失败、不回滚——渠道保持启用，真实流量拿诚实 403，15:00 重置即秒回）；`PROBE_INCOMPATIBLE_MARKERS` 增 `"请使用最新版的codex客户端"`（SharedChat 对非真实 codex 客户端一律此 403，探针无健康结论）。`test_guardian.py` **189 用例全绿**（新增 3：busy 不计数、自愈额度不回滚、codex-client 拒收不计数）。

**400 `invalid_encrypted_content` 定性（14:14 修订）**：13:59:46 codex 请求路由 127（预算池 503）→ **126（当时已复活）→ 上游 400 外来加密 reasoning 块**。codex Responses 无状态模式会全量回放含加密 reasoning 块的历史（按上游 org 加密）；会话中途换上游 → 外来块被 400 拒收。126 挡在 128 前时 400 冒泡直达用户（NewAPI 视 400 为终端错误不再 failover）。**处置修订**：126 的 400 是内容校验（模型层已放行），非模型缺失——**14:14 应用户指正重新启用 ch126（readback status=1）**；"上游不供 astra"为误判，见勘误。若会话中途混过 126/其它上游的块 → 双上游全拒 = 会话永久中毒，**唯一解=新会话**。15:00 前全站额度空窗为结构性现实（SharedChat 上游整时段耗尽），恢复循环将自动回池。

**备份/回滚基线**：本次补丁前未新建独立 pre-patch bak（协议偏差；最近回滚点为 `bak-20260912-busy-probe`，缺 busy hunks 需按本文档重打），已落 post-patch 已知良好快照 `guardian.py.bak-20260912-stability-postpatch`。

**进程管理教训**：schtasks 任务真名 `\NewAPI Guardian`（非 omp_guardian）；`/end` 不杀孤儿 pythonw（PID 24908 存活至手工 taskkill），重启后必须按 PID+CreationDate 复核单实例（新实例 5392 @14:09:48）。

### 勘误：ch126「上游无 astra」结论推翻（14:14–14:16，应户指正）

**误判根因**：NewAPI SYS 管理探测走 **chat/completions 面**，3×404「当前 API 不支持所选模型 gpt-6-astra」；而 codex 真实流量走 **`/v1/responses` 面**——13:59 的 400 `invalid_encrypted_content` 只可能出自**模型层已放行之后**的内容校验（模型不支持时会回 404-style 不支持错误，而非内容校验 400）。渠道配置佐证：ch126（any，base_url `https://anyrouter.top`）models 表就一行 `gpt-6-astra`，专为该模型而建。**教训：跨 API 面的探测结果不能用来判定模型不存在；类型 1（OpenAI 兼容）渠道的 SYS 探测面 ≠ codex/responses 面。**

**处置**：ch126 重新启用（14:14 status=1）。当前 astra rung：126（responses 面可用）+ 128（15:00 额度重置后恢复循环自动回池）+ 127（agentrouter 放量后）。干净会话在 126/128 上均可用；混合上游历史的会话在任何上游都会 400，新会话是唯一解。运行时冒烟已验 `_is_probe_incompatible` 收紧后四例全过；引擎 PID 20204 @14:16:08 载入全部修复；`test_guardian.py` 190 用例全绿。

### 亲和粘住加固（14:22，用户批准）

**变更**：`channel_affinity_setting.rules` 中 `codex cli trace` 规则 `ttl_seconds` **300 → 1800**（PUT /api/option/ 热加载，回读验证 1800；未清 affinity cache——纪律 §9.1，活跃会话不受影响；未重启）。效果：codex 会话空闲 ≤30min 内不重钉——消除「暂停 >5min 回来落在另一个上游 org → 历史 reasoning 块全外来 → 400」这一中毒路径。

**刻意不改**（运营史证据）：`switch_on_success=true`（2026-08-16 muyuan 停摆反例：false 时成功 failover 不迁钉，会话整 TTL 钉死劣化渠道）、`keep_on_channel_disabled=false`（ch75 立即接管依赖它；改 true 换来"钉死禁用渠道硬报错"，且是全局开关会波及 claude/glm 等全部家族）。全局翻动 = 复现已知事故；活跃失败切换（pin 随成功迁移）保留为可用性优先，**中毒后果的可修复路径 = `scripts/ops/codex-resume-scrub.py`**（清外来 reasoning 项后原会话续用，不必弃会话）。

**剩余风险**：空闲 >30min 的重钉、活跃 failover 迁钉，仍可能跨上游 org → 400；急救 = scrub 或新会话。**回滚**：`C:\Users\zhugu\.omp\guardian\affinity-options-backup-20260912.json`（本次变更前快照，ttl=300），PUT 回写即可；更早基线 `C:\Users\zhugu\.new-api-local\backups\channel-affinity-20260912-034402.json`。
