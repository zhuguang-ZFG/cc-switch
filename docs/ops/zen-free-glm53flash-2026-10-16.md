# opencode glm-5.3-flash 重新免费 → 接入 ch178 并注册 OMP（2026-10-16）

用户报：opencode 又把 **glm-5.3-flash 免费**了。核实与接线如下。

## 上游证据

- `opencode models`（真 CLI 目录）新增 `opencode/glm-5.3-flash-free`（同批还有
  `ll-alpha-free`、`nemotron-3.5-super-vl-free`、`cat-jam-free`、`deepseek-v4.1-flash-free`
  等免费 id）；
- 桥侧实探（dry-run，探针缓存 30 分钟）：**`glm-5.3-flash-free` LIVE 200（14.6s）**、
  `ll-alpha-free` 200（6.5s）、`nemotron-3.5-super-vl-free` 200（6.9s）；
- 同步计划 `drop=[]`（现有 11 只全有 24h 内真实流量），无 429/无 dead。

## 变更

**ch178（zen-free-bridge）11 → 14 只**，用 house 脚本
`scripts/ops/sync_zen_free_bridge_models_20261015.py --apply`（快照 → PUT models →
ModelRatio=0 → readback → admin test，任一步失败自动回滚）：

- 新增：`glm-5.3-flash-free`、`ll-alpha-free`、`nemotron-3.5-super-vl-free`；
- 快照 `new-api-before-zen-free-bridge-recover-20261016-151430.db`（integrity ok）；
- readback：models/abilities/ratios 三项一致；admin test `glm-5.3-flash-free` HTTP 200。

**OMP `models.yml` 新增条目**（ch178 免费族，紧随 cat-jam-free）：

```yaml
- id: glm-5.3-flash-free
  compactionModel: zg-newapi/deepseek-v4-flash
  reasoning: false        # 实测：reasoning_effort/thinking 传参后仍无 reasoning 输出
  supportsTools: false    # 实测：tools 传参 200 但无 tool_calls（散文作答）
  contextWindow: 131072   # 实测 ~125.5k-token 提示 200 且中段标记读回 ✓
  maxTokens: 65536        # 实测 32768/65536 均被接受（生成上限未逐量测）
```

（与付费 `glm-5.3-flash` 条目并存：付费名走 ch208/ch118/ch183/ch173 四腿池，免费名走
ch178 单腿。）

## 验证（经网关，即 OMP 实际路径）

| 探针 | 结果 |
|---|---|
| 小请求（max_tokens=64） | 200 / 8.9s / `content='OK'`，无 reasoning |
| tools（get_weather, tool_choice=auto） | 200 / 27.8s / **无 tool_calls**，散文作答 |
| `reasoning_effort: high`、`thinking: enabled` | 200，仍无 reasoning 输出 |
| 上下文标记（~125,568 token） | **200 / 15.8s / 标记 `ZEN-GLM-MARKER-7741` 读回 ✓** |
| max_tokens 32768 / 65536 | 均 200（接受） |
| `verify_omp_newapi_alignment.py` | **CLEAN** |
| `test_omp_routes` | **40/40 OK** |
| 冒烟 | **FAIL 仍 5 类、无新增**（3 个新 id 带 ratio 0） |

## 「zen 做主力」（用户指令，同日 15:25）

指令原文：「zen做主力」——即让 zen 免费腿成为该 id 的**主腿**（回答上文待办第 1 条）。

**做法**（`scripts/ops/make_zen_primary_glm53flash_20261016.py`，dry-run 默认、失败自动回滚）：

- ch178 models += `glm-5.3-flash`，并加 `model_mapping {"glm-5.3-flash": "glm-5.3-flash-free"}`
  （桥只认免费 id，付费名经映射转发）；
- ch178 priority **0 → 30**（高于付费池最高腿 ch118 p25）→ 该名默认落 zen；
- 波及面已核：ch178 的 15 个 id 中仅 `longcat-2.5-preview-free` 另有他渠活腿
  （ch133 p1），抬高后同样变为 zen 优先——与「主力」意图一致。

**证据**（全部实测）：

| 检查 | 结果 |
|---|---|
| readback | models 15、mapping 含新键、priority=30、**key 哈希未变**、**channel_info 未变**、ability (1 行, enabled=1) |
| admin test `ch178/glm-5.3-flash` | HTTP 200 success=True |
| 网关实弹 `glm-5.3-flash` | 200 `content='OK'` |
| **归因稳定性** | 5 分钟窗口内 **6/6 请求全落 ch178** ✓ |
| **延迟观察** | 同句小请求实测 **8.5s / 66.3s / 137.2s**（免费层排队/节流特征；冷启时 6-15s 为常态）→ tiny/advisor 若对延迟敏感需复核，杠杆=ch178 权重/优先级或降为备档 |
| 门禁 | alignment CLEAN、`test_omp_routes` OK、冒烟仍 5 类（无新增） |
| 快照 | `newapi-before-zen-primary-glm53flash-20261016-152542.db` |

**house 事实（本次顺带确认）**：`GET /api/channel/` 返回的 `key` 是**空串**（sha=e3b0c442…），
且 fork 对 PUT 传入空 key **保留原值**——这正是同步脚本今天没打坏 ch178 的原因。本脚本
仍按规约**从 SSOT 显式 hydrate key** 并读回哈希校验，避免依赖该隐式行为。

**能力注意点（已写入 OMP 条目注释）**：免费主腿**不支持 tools**（tools 传参 200 但无
tool_calls）、**无 reasoning 输出**（`reasoning_effort`/`thinking` 传参被忽略，仍正常作答）、
免费日额度用尽时桥返回 **429** —— 按 house 既有设计（smoke 注原文：**08-11 起 429 被
刻意移出重试表**，"429 透传交客户端退避 + 回退链；重试仅覆盖瞬时类状态码"），
**NewAPI 不会因 429 换腿**，而是由 **OMP 的 `fallbackChains` 降级到下一模型**（smol 链：
glm-5.3-flash → deepseek-v4-flash → mercury-2 → muse-spark-1.2-contributor-free）；
付费腿（ch118/ch173/ch208）只在 408/500-503 类可重试错误上按优先级接替。付费腿的
`reasoning: true` 声明保持不变（本 id 的 reasoning/thinking 声明按付费腿能力写）。

**顺带解决的历史问题**：`pool-error-triage-2026-10-13.md` 当时记录「glm-5.3-flash 有效
容量=2 条 5h 窗口福利腿，OMP tiny/smol 兜底流量全压这里」并留了「找非窗口型 glm-flash 腿」
的待办——zen 免费腿正是**非窗口型**，本次改动即该项的落地。

**OMP 侧**：`glm-5.3-flash` 条目 name 串更正为「zen ch178 p30 主档; ch118 p25 / ch173 p20 /
ch208 p5 备档」（原文案错误地写着「4腿池 ch208 p30 / ch118+ch183 p25」——ch183 早已
enabled=0、ch208 实为 p5）；新增注释记录上述路由与能力注意点。**OMP 重启后生效。**

## 备注与待办

- **需 OMP 重启**才会读到 models.yml 新条目（渠道侧即时生效）；
- `ll-alpha-free`、`nemotron-3.5-super-vl-free` 已进 ch178**但未注册 OMP**（可用但未挂；
  要挂需各自实测能力后加条目，属后续按需事项）；
- 免费额度用尽时桥统一返回 **429**（house 语义：420/429 属配额、非故障，勿据此判死）；
- 该入口与付费 `glm-5.3-flash` 是不同 id：若想让既有付费名的请求优先走免费腿，需要
  在渠道里做 `model_mapping` 别名或改 OMP 路由——**属用户裁决项，本次未动**。
  （2026-10-16 晚更新：`deepseek-v4.1-flash-free` 经裁决**不推广**——桥为纯文本单飞，
  见下节；`ll-alpha-free` / `nemotron-3.5-super-vl-free` 已注册 OMP。）

## 重启后验证与四裁决（2026-10-16 晚，OMP 重启后）

**重启生效**：OMP 新实例 pid 8472 于 15:35:02 启动（晚于配置提交 881b895 的 15:34:20；
`models.yml` mtime 15:34:20 < 启动时间 → 注册表即新文件）。启动日志确认 compaction r13
（`global compaction model reconciled … revision=2026.10.15-r13`）与 MCP 刷新。

**主腿归因（重启后实弹）**：15:35–16:03 `glm-5.3-flash` 完成请求 11 条：**9 条落 ch178**
（zen），2 条落 ch118（单飞桥占满/超时时的付费接替，189s/179s）。归因取自 `logs` 表
`channel_id`，与上一轮 6/6 结论一致。

**新发现：zen 主腿让 advisor 静默失能（关键）**

- advisor（角色=glm-5.3-flash）是带工具的评审器（read/grep/glob/advise）；zen 桥是纯文本
  单飞转发（`promptFromMessages` 只吃 messages，tools 数组从不转发）→ 经 ch178 的评审
  响应不可能带 tool_calls；
- 实测（本会话 `__advisor.jsonl`）：重启后前 11 次评审全部落 glm-5.3-flash 名下
  （ch178 ×9 / ch118 ×2）：**仅 ch118 那次带 read+grep，其余 0 工具调用、0 advise、0 注记**。
  对照旧会话今日 103 次评审（glm 池 ~8% 成功 → 链把 90 次兜到 deepseek-v4.1-flash）
  产出 20 次 advise；
- 机理解释：zen 把 glm-5.3-flash 的失败面"修好"（200），恰好切断 advisor 依赖的兜底路径
  （链=[deepseek-v4.1-flash]），评审从此静默——这不是延迟问题，是工具链结构性失效。

**四裁决（用户确认）**

1. **工作马不推广**：`deepseek-v4.1-flash` 不加 zen 主腿。桥=纯文本单飞（tools 不转发）、
   150s 超时、日配额 429；实测 `deepseek-v4.1-flash-free` 单字回复 157.1s（含 150s 桥超时
   重试）——结构性不可承载主循环。
2. **advisor 重指 `deepseek-v4.1-flash`（用户签字特批）**：修订 2026-08-20/09-09
   "advisor 只走免费/低成本路由"约束。改动：`config.yml` `modelRoles.advisor` →
   `zg-newapi/deepseek-v4.1-flash`；`fallbackChains.advisor` 自指改为
   `zg-newapi/deepseek-v4-flash`（初版曾选 k3 作"强模型兜底"，按约束精神复核后修正：
   k3 ratio=2（表内最高档）与"低成本"相悖；dsw-flash ratio=0.5、实测工具可用
   （2.7s/3.3s、tool_calls ✓）、池 ch180/ch118/ch127 多腿）；门禁
   `test_advisor_role_stays_on_approved_low_cost_routes`
   白名单加一行 + 日期注释。实体理由：deepseek-v4.1-flash 是当日 90 次评审的**实际载体**、
   本地 `ModelRatio=0`（6h 实测 548 条请求 quota=0）、工具链实证可用。
   **热载实测**：config.yml 写入（16:05:41）后 15 秒（16:05:56）advisor 已切至 deepseek，
   恢复 read/grep/advise（16:06–16:10 递出 3 条注记，含本条约束的 blocker）——
   **advisor 角色的 modelRoles 变更会话内即时生效，无需重启**；其它 config.yml 面
   （chains/其它角色）是否同效未逐一验证，上节"需 OMP 重启"的旧结论仅在此项上被修正。
3. **冒烟契约放宽**：`BACKUP_CHANNEL_POSTURES[178]` `max_priority` 0 → 30（注释注明
   2026-10-16 用户指令 zen 主腿），weight≤5 保留；重跑冒烟恢复旧违约集。
4. **两个新免费 id 挂 OMP 条目**：`ll-alpha-free`、`nemotron-3.5-super-vl-free` 按
   glm-5.3-flash-free 模式注册（`supportsTools: false` / `reasoning: false`，context 保守
   标注未量测），**不动任何路由**。

**新免费 id 实测数据**（经网关→ch178→桥→opencode CLI）：

| id | 小请求 | tools | reasoning | 备注 |
|---|---|---|---|---|
| ll-alpha-free | 200 / 8.7s / `OK` | 200 无 tool_calls（散文） | 无 reasoning 输出 | 长/工具形态打满 150s 桥超时后重试（160s+） |
| nemotron-3.5-super-vl-free | 200 / 8–38s / `OK`；`17*3`→`51` ✓ | 200 无 tool_calls（散文） | 无 reasoning 输出 | VL 能力经桥不可用（桥对 image parts 拒收） |
| deepseek-v4.1-flash-free | 200 / 157.1s / `OK` | （结构性无 tools） | — | 150s 桥超时上限下不可用为主力 |

**门禁重跑（全部改动落盘后）**：`test_omp_routes` **40/40 OK**；`verify_omp_newapi_alignment`
**CLEAN**；冒烟 5 类不变（backup posture 回 182 单条）。`perf_metrics` 15:00 桶已落盘：
`glm-5.3-flash` **req=109 ok=24**（上四桶 4/59、9/67、5/43、6/114，同为尝试级/完成行口径）。
**读数限定**：ok≈`logs` 完成行数（24≈25 行），req 是尝试级计数（约 85 次失败不落 `logs`），
桶内 25 行里 5 行属 15:00–15:25:42 改前段——**不可读作干净的改前/改后成功率对比**。
免费侧同桶：`glm-5.3-flash-free` 8/8、ll-alpha 3/3、nemotron-vl 3/3。

**变更文件**：cc-switch `scripts/ops/newapi-local-smoke.py`、`scripts/ops/test_omp_routes.py`、
`scripts/ops/test_omp_sota_snapshot.mjs`、本文；OMP 仓 `config.yml`（advisor 角色+链）、
`models.yml`（两个免费 id 条目）、`extensions/omp-sota-escalation.js`（基线探测 8s 预算+探针）。

## 收尾加固：agent 卫生（同日晚，独立于 zen 主腿）

审计发现两类配置缺陷并修复（OMP 仓 `b6240e8`）：

- **`retry.fallbackChains.smol` 改工具安全三口**。原链首跳 `glm-5.3-flash` 如今由
  ch178 p30（zen 主档，纯文本桥、tools 不转发）优先命中——u2-flash 一旦失败，
  scout/librarian/sonic 会在兜底后静默失去工具调用。新链
  `deepseek-v4-flash → qwen3-8-27b → glm-5.3`；三口均经网关带 tools 探针实测
  （`tool_calls` ✓；独立复核复测 4.7s / 0.6s / 5.7s）。u2-flash 免费腿
  2026-10-31 到期的风险由新链承接。
- **`structured: true` 激活三个惰性 schema**（reviewer / security-reviewer / librarian）。
  `task.structured` 默认关闭时，逐任务 spawn 既不下发 schema 也不给 `yield`
  ——三项声明此前形同废弃（三个代理的 prompt 却明写要求 yield 载荷）。agent 级
  structured 在全局关闭时依旧生效；实弹 reviewer 子代理复核本批 diff 后返回
  结构化 payload（schema valid，`review_status=completed`，diff 四项全 PASS）；
  librarian（@smol/u2-flash 免费小模型 + 较大 schema 的最可能不符点）经 2m24s
  实弹复核同样返回合规结构化载荷（抽取的 allow-list 与日期串与 ground truth 逐字一致）。
  **验证上限**：链内实际穿越未触发（fallback 走 reset/cooldown 为既有文档语义）；
  u2-flash 2026-10-31 到期后的落链行为是文档外推，到期时应实测确认。

同批工具能力复测：k3（6 子代理 + designer/task 共用腿）`tool_calls` ✓；
`@slow`/`@plan` 唯一兜底 `intern-s2-preview` `tool_calls` ✓（其 160-token 档正文为空
是推理截断假象，2048 档正常）。`test_omp_routes` 40/40、`verify_omp_newapi_alignment`
CLEAN、`test_omp_hutuji_workers` 2/2 复跑通过。


## 用户报障：sota-escalation 的 `before_agent_start` handler 超时 30s（2026-10-16）

**症状**（用户报）：使用中反复出现
`Extension "...omp-sota-escalation.js" error: handler timed out after 30000ms`。

**日志证据**：`~/.omp/logs` 全量扫描命中 **20 条**，全部 `event=before_agent_start,
timeoutMs=30000`，跨 5 个 pid（9380/15044/21708/18276/12920），时间 2026-10-15 23:35 →
2026-10-16 17:44；个别会话几乎每轮一次。30s 超时窗口内进程无其它活动日志。同一代码路径在
本会话（cc-switch）与 `omp -p` 冒烟实例均不触发。

**定位**：该 handler 唯一的 await 是基线快照（`pi.exec("git",…)` 三连 + `hash-object`，
单次 15s 上限）。两个疑似工作区（STM32/monidianlu）实测 git 均 0.05–0.10s；hutuji 的
`ls-files --others` 6.8s（76,160 个未跟踪文件；批量 `hash-object` 会撞 Windows 命令行长度上限
WinError 206，但 40 文件上限下该错误走 catch 直接降级，不会进逐文件回退）。排查还证伪两条：
① 日志仅覆盖 10-12 起、最早超时 10-15 23:35，但基线探测代码自 **08-20**（`384d569`）即存在
——**不是代码回归**，是宿主侧状态变化；② 全量日志中 ≥10s 的 `ui.loop-blocked` 仅 3 条，与
20 次超时不同 pid 不同时刻——**不是事件循环整体卡死**。→ 判定：这些会话里
`pi.exec("git",…)` **未在预算内 resolve**（30s 花在等待而非 CPU）；宿主侧 stalling 的
确切原因**保留为未知**，已加探针待现场取证。

**修复**（OMP 仓 `b8aaccc`，取代同日 `35da154` 的"8s 包装"版）：改为**结构性去阻塞**——
`before_agent_start` 只 `startBaselineProbe()` 启动、**完全不等待**；探测值在 `agent_end`
的 managed timer（`ctx.setTimeout`，不受 30s handler 预算约束）里经 `resolveSnapshot()`
以 8s 截止消费，超时降级 `complete:false`（`changedFilesSince` 按"回合末变更集"处理，仍受
`MAX_CHANGED_FILES=40` 约束）。救援路径（`tool_result`）与 `/sota*` 命令的分钟级
`runEscalation` 同样改为 `scheduleBackground(ctx, …)` 派发。诊断 warn：单次 git 调用 ≥2.5s →
`sota: git probe slow`；消费超时 → `sota: snapshot incomplete {label:baseline|end}`。

**验证**：门禁 `scripts/ops/test_omp_sota_snapshot.mjs` **14/14 通过**（handler 挂死 exec 下
0–1ms 返回；deferred 消费 8.0s 截断并带 label 告警；慢调用 2.7s 告警且不误报；救援 handler
立即返回；降级语义不变）。**真机取证**（advisory 建议）：临时把阈值降到 1ms 跑 `omp -p`，
日志出现两轮 `sota: git probe slow`（18:11:49 基线 / 18:11:54 agent_end 消费，单次
59–77ms）——证明新路径在真机执行且 deferred 消费确实跑过；随后阈值还原 2500 并复跑门禁。

**未解/待观察**：`pi.exec` stalling 的宿主级原因（借新探针取证）；救援/显式命令脱管后
`deliverAs: steer` 在回合已结束时能否投递需实测（今日日志未出现 `event=tool_result` 超时，
暂无现场）。

**附带发现（本批链头探针）**：`@task`/`@designer` 兜底链的 Opus 头全部不健康——
`claude-opus-4-8` 启用腿仅 ch72（anyrouter）立即 429；`claude-opus-5` 探针 200 但 136s 空流
（无 choices 帧，**结论=挂起/掐断，未定**，不可读作"不支持工具"）；smol 链原第 3/4 跳
`mercury-2` 429（free tier at capacity）。三者均为 k3 失效时的逃生口，建议后续替换或评估降级。
**更正（同日 18:4x，用户指出 + 实测复核）**：Claude 腿**不是"死"**——anyrouter ch72 启用中
（探针即时 429 = 容量拒绝），agentrouter claude 池会**定时复活**；详见下节。

## 顺带巡检：NewAPI 与 Kimi Code（同日晚，"继续改进优化"）

**NewAPI（本地 3002）**：24h 内**无错误记录**（`logs` 仅 type=2 消费 4416 条、type=3 管理审计 46 条）；
104 渠道 = 31 启用 / 72 停用 / 1 自动禁用（ch70 vip-j3gb-gpt，weight 0 的 gpt-5.4-5.6 死池）。主路由全绿
（17:00 桶：deepseek-v4.1-flash 224/224、k3 36/36、u2-flash 6/6、deepseek-v4-flash 7/7、qwen3-8-27b 1/1）。
**glm-5.3-flash 池观察落定**：advisor 16:05 迁走后，16:00 桶 1/1、17:00 桶 4/4（此前 4–24%）——池已只剩
轻量杂活流量且健康。
**唯一实质问题——opus 逃生口失效**：`claude-opus-5` 启用腿仅 ch3（baibei，p52 多 key，平均响应 34s，
实测 136s 空流）+ ch72（anyrouter，历史从未通过渠道测试、即时 429）；`claude-opus-4-8` 启用腿仅 ch72；
`mercury-2` free tier 满 429。perf 桶佐证（17:00：opus-5 3/0、opus-4-8 1/0、mercury-2 1/0）。
**未改任何路由**（属采购/策略决定）；OMP 侧 task/designer 链保持不动，仅标注。

**Kimi Code（kimi 2.2.0，`KIMI_SHARE_DIR=~/.kimi-code`）**：
1. **新增免费主力模型** `newapi/deepseek-v4.1-flash`（provider=local-newapi；1M 上下文、thinking+tool_use、
   low/high/max；本地 ratio 0、三腿健康），加入 `secondary_model` 清单；修正 `deepseek-v4-flash` 的
   "免费"措辞（实际 ratio 0.5）。改前备份 `config.toml.20261016-181548.bak`。
   实弹：`kimi -p "Reply with exactly: OK" -m newapi/deepseek-v4.1-flash` → 3.5s 返回 OK。
   （`-p` 与 `-y/--auto` 互斥，CLI 级工具调用未能实测；该模型的 wire 级 tool_calls 已在网关验证。）
2. **"17 个技能全部不可见"已修**：CLI 的用户技能根随 `KIMI_SHARE_DIR` 走（`~/.kimi-code/skills`，不存在）
   另加共享根 `~/.agents/skills`；而 omk 安装到旧路径 `~/.kimi/skills` → 6 个 omk 技能
   （clarify-first/insights/omk-ralph/omk-review/requirements-elicitation/ultrawork）从未被加载。
   以 **junction** 桥接：`mklink /J ~/.kimi-code/skills ~/.kimi/skills`（非破坏，`rmdir` 即回滚）。
   实测发现技能数 **11 → 27**，6 个 omk 技能全部出现（后续 omk setup 重装到旧路径也自动可见）。
3. 待用户决定：`omk doctor` 显示 `plugin_installed=false`、`hooks_installed=false`（只装了技能）——
   要启用 omk 的插件/hooks 需 `omk setup`（会引入自动 hooks，未擅自执行）。

**sota 扩展的仓内部署源已同步（同日补）**：`scripts/ops/omp-sota-escalation.js` 是
`deploy-omp-sota-escalation.ps1` 的部署源——此前修复只落在 live 文件上，跑一次 deploy 会把 live 改动回滚。
现已把 live 版本（含本日全部重构）同步回仓，并按新语义更新仓内测试 `test_omp_sota_escalation.js`
（rescue / skip / 同回合守卫三处改为"先跑捕获的 timer 再断言"）。**复跑全绿**：仓内 sota 套件 **25/25**、
deploy 套件 **1/1**、快照门禁 **14/14**、hutuji **2/2**、routes **40/40**。deadline 计时器加 `.unref()`
（宿主会话恒有其它句柄；纯 node 测试侧用 keep-alive 固定事件循环）。

**Kimi 其它模型条目核活**：`newapi/gpt-5.6-sol` **活着且可工具调用**（3.1s 文本 / 2.7s `tool_calls`，
jojatoken 低优先腿）——可作为 opus 死腿之外的备选；`codebuddy-direct`（100.83.32.95:8787）**连接被拒**
（WinError 10061，VPS 服务未监听）——待用户决定恢复该 VPS 服务或从 Kimi 配置摘除。

## 同日晚：advisor 换强模型 + Claude 腿实况更正 + Kimi 残留清理

**advisor 走强模型（用户指令）**：原话「advisor 用 gpt 和 claude 以及 k3 这种强模型，至少也要是
glm5.3」——2026-08-20 起的低成本约束就此解除。落地：`modelRoles.advisor = zg-newapi/k3:high`
（ch33 p50 活腿，带 tools 探针 3.4s tool_calls），兜底链 `gpt-5.6-sol → glm-5.3`（实测
2.7s / 2.9s tool_calls）。门禁白名单与注释同步修订（记日期与依据）。**热载实证**：改动后约 1 分钟，
运行中会话的 advisor 已切换（transcript 18:35:21 起 5 条 k3，其中 2 条 advise 载荷落账）。
**Claude 未入链**：agentrouter 上游对短流式请求有敏感词误杀（`500 sensitive words detected`，
2026-08-03 实测）——对每轮高频短请求的 advisor 是已知故障类；等有稳定（启用且非 429）的 Claude
腿再评估。

**Claude 腿实况更正**：anyrouter ch72 启用、载 `claude-opus-5`/`claude-opus-4-8` 与
`zg-agent-claude-opus-5` 等别名（后者只挂 ch72，可作 anyrouter 独占探针）；18:4x 实测返回即时
**429 `{"type":"error","message":"Service Unavailable"}`**——与 10-15 admin 体检同款判定
（"任何router **窗口型**"），属**间歇/窗口**而非死；agentrouter claude 池 ch86/134/135/136 当前
st=2，但 test_time 为 10-15 23:00 / 10-16 00:17，且当日 **10:54 的 opus-5 真实流量即出自
ch134-136**——"定时复活"有据。

**Kimi 清理**：`providers."codebuddy-direct"` 与 `[models."codebuddy/gpt-5.6-sol"]` 已从
config.toml 删除（该渠道 2026-08-12 已退役：WorkBuddy 上游流式 11140 不可用；guardian /
proxies-supervisor / newapi-local-smoke 三处早已带注释同步清）。备份
`config.toml.20261016-183618.bak`；现存 **3 provider / 9 模型**，tomllib + `kimi -p` 实弹均 OK。
**顺带记录**：本轮 `config.yml` 的 `default` 角色现为 `glm-5.3:max`（用户侧改动，非本批所为）。

## 同日晚二：agentrouter 钥匙池补充 + 带图请求故障修复

**agentrouter 池补充（用户提供三把自账号 key）**：合并进
`~/.kimi-code/proxies/agentrouter-proxy/keys.json`（2 → 5 把；备份
`keys.json.bak-20261016-184834`；只记 SHA-256 前 8 位指纹 `f53a2b30/41331350/f4cdf4c0`，
key 原文不入文档/提交）。代理按 mtime **每请求热载**池文件（无需重启）；
实测：网关 `deepseek-v4-flash`（ch180 p51 → 8788 代理）**200 / 3.5s / "OK"**。
判别实验：带图单发请求对 `deepseek-v4-flash`（3.6s）与 `deepseek-v4.1-flash`（2.8s）均 200
——**18:45 那批 104 条消息的 `rejected as invalid` 与图片无关**（上游对特定大请求的姿态，
key 被误冷却属代理归一化行为，留观察）。

**带图请求故障（用户报 glm-5.3-flash 调用不成功）**：根因链——会话历史上游第 47 条
（tool result 附图，`read` 图片）永久带图 → `glm-5.3-flash` 主腿（zen 桥 ch178）对
image parts **即时 400** 拒收 → OMP 整模型降级；同族 `glm-5.3` 经网关直接 **404
"No endpoints found that support image input"**（池内无图端点）。`glm-4.6v`（真视模）当前上游
522 不可用。openai-completions 的剥图开关是 **`compat.stripImageInput`**（默认由目录类规则给，
自定义 provider/id 不命中 → 图片被照发）。修复：对**无图端点**的 5 个 id 显式声明
`compat.stripImageInput: true`（`glm-5.3`、`glm-5.3-flash`、`glm-5.3-flash-free`、
`ll-alpha-free`、`nemotron-3.5-super-vl-free`；均带日期与实测依据注释）；
deepseek 两 id 经实测可收图，**不加**。**需重启生效**（models.yml 非热载面）。
受影响会话的即时缓解：对带图那一轮做 compact/rewind，历史去掉图片即恢复。
门禁复跑：`verify_omp_newapi_alignment` CLEAN、`test_omp_routes` 40/40。

**91tv key（同日用户提供）未接入**：与 chengapi 同口径——来源不明的共享凭据不进生产路由；
若为用户自有账号 key，确认后按"先建停用渠道 → 探针验证 → 健康才开"的房式流程接入。

**motomoto（ch225，用户当日 14:47 新建）**：上游 `https://motomoto.lol` 自己也是 NewAPI 站，
18:4x 对其 4 个模型（gpt-5.5 / gpt-5.6-sol / gpt-6-astra / codex-auto-review）实测**全线 503
「维护中，请稍后再试」**；`ch225` 因此从未通过测试（test_time=0）、abilities 全关，
`st=2 + auto_ban=1` 恰是房式 park 姿态——维持现状即可，等上游复血再探一次开。
自动回捞属 10-15 已标"待裁决"的策略项，未擅自新建。**若将来要建复探哨兵**：ch3 版的
`classify_probe` 是 Anthropic-SSE 专用判据（`content_block_delta`/`text_delta`/`message_stop`），
ch225 是 type-1 OpenAI 渠道（`{"choices":[{"delta":…}]}` + `[DONE]`）——照抄会永远判失败，
须换判据或改非流式探测。

## 同日晚三：聚合面"好多模型报错"根治（qwen3-8-27b 四层 + opus-5-5 实况）

**qwen3-8-27b（translator/tiny 链在用）逐层实测修复**——判据是
`omp -p --model zg-newapi/qwen3-8-27b` 的日志：从 400 到 **0 provider 错误**。

1. **`chat_template_kwargs`**：Qwen 3.8+ 目录规则给历史回合发 `preserve_thinking`（顶层 +
   template kwargs），上游 400 "property 'chat_template_kwargs' is unsupported"。实测 body 已抓
   （`http-400-requests/1792148534766-*.json`）。修：id 级 `compat.qwenPreserveThinking: false`
   + `qwenTemplateReasoningEffort: false`（后者关掉另一 kwarg 源）。
2. **`prompt_cache_key`**：OMP chat/completions **恒定注入**，models.yml **没有可用抑制键**
   （形态未声明 `supportsPromptCacheKey`，覆盖被 `applyCompatOverrides` 丢弃——实测设 false 无效）。
   修：**渠道级 param_override 删除**（ch88 既有先例），新脚本
   `scripts/ops/apply_qwen_leg_param_override.py`（身份校验→在线全库备份→key 回填→PUT→readback）
   应用到 **ch124(groq) / ch171(obitmc-relay)**；备份 `new-api-before-qwen-leg-override-20261016-190833.db`。
3. **`max_completion_tokens ≤ 16384`**（groq 硬限）：声明 32768 → 400。修：id 级 `maxTokens: 16384`
   （按最严腿收齐）。
4. **413 Request too large**：groq on-demand 档 TPM 容不下 OMP 的大 system prompt（直探 groq
   至 16384 全 200，说明非取值问题），而 **413 不在可重试码** → groq 当主腿时整条硬失败。
   修：ch124 优先级 **50 → 45**（runinfra ch88 p49 主服务；脚本内 readback 验证）。

**opus-5-5（用户："agentrouter 开闸了还用不了"）**：ch86 `agentrouter-claude` 已启用并在服务
（网关探针 200，**tool_calls=YES**），但 **TTFT ≈ 139s**（ch86 自身 use_time 144s；同渠道另有
11–15s 样本 → 波动极大）。ch3（p52，abilities 在册）未出现在服务日志里。OMP 侧选择器是
**`zg-newapi-anthropic/claude-opus-5-5`**（写 `zg-newapi/…` 会不匹配，OMP 会贴候选）。
另：该模型的 ModelRatio 已按同族补 **0.5**（见下段），不再是"未注册"状态。

**未注册 ModelRatio（smoke FAIL）**：修前 4 名按默认 37.5 计费 → 其中三个按**同族既有值**补齐
（`claude-opus-5-5`=0.5 同 opus 族、`kimi-k3-oc`=2 同 k3 族、`qwen3-8-27b`=0.5 同 qwen 族；
经 `/api/option/` PUT，写前快照 `new-api-before-modelratio-20261016-192534.db`，写后逐键 diff
仅 +3 无删改），复跑 smoke 该项只剩 **`jina-embeddings-v3`**（无同族先例，待用户给值）。

**glm-4.6v 视模全灭（req=37 ok=0）已处置**：唯一腿 ch185（0v0.club）实测 **HTTP 522**
（上游挂）。候选用**合法 PNG 实弹**（自造 2x2 红/蓝；此前用的小 PNG 会被部分上游判非法——
探针方法已修正）：`dots-3-note-prev`（ch77，714ms，当日 19:25 测活）3.1s 正确识别、
`gpt-5.6-sol` 2.6s 正确识别。→ `modelRoles.vision` 切到 `zg-newapi/dots-3-note-prev`（本就在
vision 链内），链改 `[intern-s2-preview, glm-4.6v]`（不重复主选，保留回血中的 0v0 腿）。
modelRoles 属已证热载面，运行中实例即时生效。门禁：routes 40/40、alignment CLEAN。

**ch86 空流+计费（"opus-5-5 用不了"的真形态，19:30 取证）**：smoke 的
`zero-output billed streams` 由 OK 转 FAIL，指纹 = `completion_tokens=0 且 use_time≥120s 且 quota>0`
（规则注释里 2026-08-20 已有 ch86 同款事故）。实况：**ch86 ×8 条，全是 `claude-opus-5-5`，
use≈138–149s，ctok=0，quota 合计 19,928,176**（ptok 2 万~20.5 万，含整段会话的大请求）。
即：ch86 高频"跑满 ~140s 后吐空流且照常计费"。而 `modelRoles.default` 现为
`zg-newapi-anthropic/claude-opus-5-5:max`（用户设置），default 按门禁**无兜底链**（硬失败）
→ 每轮 2–3 分钟空转，这就是"用不了"的体感。**建议（未擅自改，属用户偏好）**：把 default 换回
快的强模型（如 `zg-newapi/k3:max`），opus-5-5 留给可等待的场景；
**更正（20:25 复核后）**：坏腿摘除后 opus-5-5 实测 **200/7.2s/tool_calls ✓**——`default` 保持
opus-5-5 已无性能问题，上述"换回 k3"的建议**不再必要**（保留字段作历史记录）。若坚持 default=opus-5-5，则门禁不允许给它加链（`test_default_role_has_no_model_fallback_chain`
只豁免两个已批名）。

**纯客户端剥图的 OMP 级验证（bg_10，同日 19:47，复核 #19 点名项）**：新进程
`omp -p "@D:/tmp/redblue.png" "Name the two colors…" --model zg-newapi/glm-5.3-flash` →
**0 provider 错误、无 400 dump**，且**答对 "red, blue"**。即两层配合成立：`stripImageInput`
让图片不再进 zen 桥（不 400），图片由 **vision 角色（dots-3-note-prev）** 描述后作为文本注入
（答对即证）。对照组（image-capable `dots`，同一张图）同样答对 "red, blue"。

**gpt-6-astra 带图 400（同批新发现）**：dump `1792151165887-*`（19:46）——
"Expected 'detail' field in image_url dict to be one of ['low','auto','high']"；其头号腿是
agentrouter ch134（p50，用户刚开）。文档无"补 detail"旋钮 → 按同法给 `gpt-6-astra` 声明
`compat.stripImageInput: true`（第 6 个；图片走 vision 角色，已由上条证明照答）。需重启生效。

**"~140–150s 切点"定层（19:40 实验，逐个排除）**：`relay_timeout=900` → NewAPI **不是**切点；
`omp -p` 曾在 **212s 成功** → 不是 OMP 的 `streamFirstEventTimeout(300s)`；最关键的一条：
**54k-token 大请求实测 150.0s 完整返回 "OK"（ch136 服务）** → 上游只是慢，不是死。综上，切点=
**客户端事件间隙看门狗**（全局 `providers.streamIdleTimeoutSeconds=60`）在 139–150s TTFT 下掐流，
8 条 `ctok=0` 计费行即此形态（客户端断开仍计费，"client_gone"）。修：`claude-opus-5-5` 条目加
`compat.streamIdleTimeoutMs: 240000`（文档口径 "stream-watchdog idle-timeout **floor**"，按模型粒度
抬下限，全局不动；同旋钮也可用于 opus-5 系其它慢腿）。models.yml 非热载 → **需重启**；
运行期效果在下一次慢 opus 回合可见。若要对所有 provider 放宽：
`omp config set providers.streamIdleTimeoutSeconds 240`（影响全局失败检测速度，未采用）。

## 同日晚四：opus-5-5 定案——真凶是 **ch3 停滞腿** + ch86 坏 key

**症状**：opus-5-5 每轮 ~140s 后拿到"空流"（网关 raw 抓包：HTTP 200 + SSE `: PING` 心跳 +
**裸 JSON** `{"error":{"message":"Insufficient account balance"...}}`——我此前的解析只认 `data:` 行，
所以误读成"空 200"）。

**逐腿直探（带各渠道自己的 `header_override`，否则上游回 401 unauthorized client）**：

| 腿 | 结果 |
|---|---|
| **ch3 baibei-100xlabs**（p52 契约头名，6 keys） | key0/key1 均 **120.4s 读超时** ← 停滞腿 |
| ch86 agentrouter-claude（旧 key 7047edcf） | **SSL UNEXPECTED_EOF**（连接被切）← 坏 key |
| **ch134/135/136**（你新加的三把：8494aa2a/22ccd1e1/3b9f9fdb） | **全部 200**（5–9s，流式/非流式都通） |

即：三条健康腿被**两条坏腿挡在前面**（ch3 p52 > ch86 p50），每个请求先吃 ~140s 停滞。

**处置**（都带写前快照/回读）：
1. **park ch86**：`auto_ban=1 + status=2`（房式 park；它本就在 smoke 的 accepted_disabled 名单）
   —— 快照 `new-api-before-park-ch86-20261016-195932.db`；
2. **ch3 摘除两个 opus 模型**（`claude-opus-5`、`claude-opus-5-5`；房式"从 models 摘除"，abilities
   自动重建、**通道姿态 pin 未动**，`CRITICAL_ABILITY_POSTURES` 只钉 ch83/91 的 sol 不受影响）
   —— 快照 `new-api-before-modelratio-20261016-192534.db` 之后另有一份 park 快照；
3. **验收**：`claude-opus-5-5` 经网关 **200 / 8.9s / "OK" / fr=stop，落 ch136**（此前 140s 空流）。

**复核反证（20:2x）**：担心"OpenAI 形态探针打不准 Anthropic 型渠道"——实测 ch3 是 **type=1（OpenAI 型**；
type=14 的是 ch86），且用 Anthropic 形态 `POST /v1/messages` 复探 ch3 → **502「Upstream request failed」
135.1s**：**两种形态都坏**，不是探针形状问题，处置成立。验收探针走的正是 NewAPI 的 openai→anthropic
转换路径并 8.9s 返回 "OK" ✓，转换路径本身健康。

**回滚**：ch86 `POST /status {"status":1}`；ch3 把两个 opus 名加回 `models` 再 PUT。
**遗留观察**：ch3 仍载 `claude-fable-5-1/5.1`（无实测证据，未动；其名注称 ch3 是 fable 主档，
若见 fable 同样停滞按同法摘除）。

### anyrouter（ch72）实况核验（20:3x，用户问"anyrouter 也没法用吗"）

- **本地桥正常**：8789 在听（日志有 `listening ... device_id=1c352ece`）、`GET /v1/models` **200/17 个模型**
  （目录里**有 `claude-opus-5-5`**）、`proxy.cjs` 对上游 500/429 有重试/轮换逻辑、无本地 429 生成分支。
- **Claude 面当前不可用**：代理日志 `POST /v1/messages -> 429` **全天连续**（08:04→20:22 CST 约 133 条，
  含我 20:21/20:22 两次实测）；返回体是紧凑的 `{"error":{"message":"Service Unavailable","type":"error"}}`
  （代理日志里 10.8KB = 上游原始大错误体，回给客户端被映射为紧凑 JSON）→ **上游侧 429**。
- **旁证**：ch72 今日**零成功消费行**（`logs` 里最后一次 ch72 成功是 10-04/05 的
  `claude-fable-5-1-reversed`）→ 它今天是真不可用；历史上能通，符合"窗口型"。
- **对 opus-5-5 的关系**：ch72 的 `models` **不含 `claude-opus-5-5`**（上游目录里有）→ 现在根本不是它的腿；
  若 anyrouter 窗口恢复，一行即可加。opus-5-5 的当前可用路径 = **ch134/135/136**（agentrouter 三把新 key，9s 级）。

**改动后横截面（20:25，tools 探针）**：`claude-opus-5-5` **200/7.2s/tool_calls ✓**（default 角色现
无性能问题）；`k3` 10.4s ✓、`deepseek-v4.1-flash` 1.9s ✓、`glm-5.3` 7.2s ✓（全带工具）；
**`claude-opus-5` / `claude-opus-4-8` 变即时 429**——其启用腿为 ch134/135/136（p50）+ ch72（p40），
429 来自 agentrouter 上游**按模型 slug 的可用性**（同腿的 opus-5-5 正常）＋ anyrouter 窗口关闭。
影响：`designer`/`task` 链的 opus 兜底跳（opus-5→opus-4-8）当前不可用，**k3 主选不受影响**。
**未擅自改**（链跳改动属策略；且 agentrouter 上游对短流式有敏感词 500 前科）——建议：把两条链的
opus 跳改指 `zg-newapi-anthropic/claude-opus-5-5`，或等上游 slug 恢复。

**designer/task 链 opus 跳修（20:30，用户问"omp 还有可改进优化的吗"）**：直探三条 agentrouter key：
`claude-opus-5` / `claude-opus-4-8` 一律 **503「当前分组 default 下对于模型 X 无可用渠道」**（上游
按 slug 不可用）；`claude-opus-5-5` 3.7–5.1s ✓。故两条兜底链改为
`[zg-newapi-anthropic/claude-opus-5-5, zg-newapi-anthropic/claude-opus-5]`（活 slug 在前、死 slug 留后
待恢复；串内无评论以免被 hand-rolled 解析器吃成候选；不入选 `FORBIDDEN_CRITICAL_CANDIDATES`）。
门禁复跑 **40/40 + CLEAN**、smoke 不变。回滚 = `git revert` config.yml 对应提交。

**门禁全集扫（20:35，"继续改进 OMP"）**：Python 7 个（`test_mirror_sync` 字节镜像、四个
deploy 套件、`test_ops_gates`、`test_omp_routes`）＋ Node 11 个（sota escalation 25/25、snapshot
14/14、problem_solving、hutuji workers、compaction model、routing observability、task verification、
unexpected stop guard、zen bridge、tool canary、bugfix cases）——**全绿**。
扫出一个**自伤回归并修复**：`test_omp_problem_solving.js` 里那条"共享 review 租约"用例同时驱动
**sota 扩展的 `/sota` 命令**，而我 20:0x 的"显式命令脱管"重构把 `runEscalation` 改成 defer 后，
该用例的 ctx 没有 `setTimeout` 捕获、断言同步 → `runs` 停在 0。已按同款模式更新（捕获 timer →
显式 `await timers[i]()` 再断言），现 8/8。**该测试文件属验证输入，改动需复核**（见提交说明）。
另：`grep -l omp-sota-escalation test_*` 穷举了全部 6 个引用者（4 测试 + 2 deploy 门禁），无其他遗漏。

## 回滚

- ch178：`cp` 快照 `new-api-before-zen-free-bridge-recover-20261016-151430.db`（停机），
  或按同步脚本的 PUT 步骤把 models 串改回 11 只 + 删三条 ModelRatio=0；
- OMP：删掉 `models.yml` 的 `glm-5.3-flash-free` 条目（OMP 仓版本化，可 git revert）；
- OMP 仓后续提交均可逐条 git revert：`config.yml`（advisor 由 `deepseek-v4.1-flash` → `k3:high`、
  兜底链 `deepseek-v4-flash` → `gpt-5.6-sol`→`glm-5.3`）与 `models.yml`
  （ll-alpha-free / nemotron-3.5-super-vl-free 条目；5 处 `compat.stripImageInput` 增补）；
- agentrouter 钥匙池：`~/.kimi-code/proxies/agentrouter-proxy/keys.json` 可恢复自
  `keys.json.bak-20261016-184834`（回退为本日新增三把 key 之前的 2 把态）；
- qwen 渠道三改：写前全库快照 `new-api-before-qwen-leg-override-20261016-190833.db`；
  逐项回退 = ch124/ch171 `param_override` 置 null、ch124 priority 回 50
  （`apply_qwen_leg_param_override.py` 只做正向，回退走 API/脚本手改）；
  OMP 侧 `models.yml` 的 qwen 条目（compat 两键 + maxTokens 16384）可 git revert；
- ch86 park / ch3 摘模：快照 `new-api-before-park-ch86-20261016-195932.db`；
  回退 = ch86 `POST /api/channel/86/status {"status":1}`、ch3 把
  `claude-opus-5`/`claude-opus-5-5` 加回 `models` 再 PUT；
- ModelRatio 三键：快照 `new-api-before-modelratio-20261016-192534.db`；或删回
  `claude-opus-5-5` / `kimi-k3-oc` / `qwen3-8-27b` 三键恢复"未注册"态；
- vision 角色：OMP 仓 `config.yml`（vision→dots-3-note-prev + 链改）可 git revert
  （回退即恢复 glm-4.6v 主选，视模将随 0v0 腿 522 继续 0 成功）；
- 契约/门禁：`scripts/ops/newapi-local-smoke.py` 的 178 pin 与 `scripts/ops/test_omp_routes.py`
  的白名单行可 git revert——但两者都是用户签字过的裁决，收回前先确认；
- agent 卫生（OMP 仓 `b6240e8`）：`config.yml` smol 链与三份 agent 文件的 `structured: true`
  可整体 git revert（回退后 reviewer/security-reviewer/librarian 的 output schema 重归惰性、
  smol 兜底重回工具不安全链）；
- sota 扩展（OMP 仓本次提交）：`extensions/omp-sota-escalation.js` 的不阻塞探测与探针可 git revert
  （回退即恢复"基线探测可耗尽 30s handler 预算"的旧行为）；
- Kimi Code：`~/.kimi-code/config.toml` 两个回滚点——`config.toml.20261016-183618.bak`
  （已含新增 `newapi/deepseek-v4.1-flash`，但 codebuddy 两段尚未删）与
  `config.toml.20261016-181548.bak`（今晚全部改动之前）；技能桥接用
  `rmdir "C:\Users\zhugu\.kimi-code\skills"` 即撤（junction，源目录 `~/.kimi/skills` 未动）。
