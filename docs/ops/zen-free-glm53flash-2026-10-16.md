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

## 回滚

- ch178：`cp` 快照 `new-api-before-zen-free-bridge-recover-20261016-151430.db`（停机），
  或按同步脚本的 PUT 步骤把 models 串改回 11 只 + 删三条 ModelRatio=0；
- OMP：删掉 `models.yml` 的 `glm-5.3-flash-free` 条目（OMP 仓版本化，可 git revert）；
- 晚间的清洁项同理：OMP 仓 `config.yml`（advisor→deepseek、advisor 链→deepseek-v4-flash）与 `models.yml`
  （ll-alpha-free / nemotron-3.5-super-vl-free 条目）可逐提交 git revert；
- 契约/门禁：`scripts/ops/newapi-local-smoke.py` 的 178 pin 与 `scripts/ops/test_omp_routes.py`
  的白名单行可 git revert——但两者都是用户签字过的裁决，收回前先确认；
- agent 卫生（OMP 仓 `b6240e8`）：`config.yml` smol 链与三份 agent 文件的 `structured: true`
  可整体 git revert（回退后 reviewer/security-reviewer/librarian 的 output schema 重归惰性、
  smol 兜底重回工具不安全链）；
- sota 扩展（OMP 仓本次提交）：`extensions/omp-sota-escalation.js` 的不阻塞探测与探针可 git revert
  （回退即恢复"基线探测可耗尽 30s handler 预算"的旧行为）；
- Kimi Code：`~/.kimi-code/config.toml` 可恢复自 `config.toml.20261016-181548.bak`；
  技能桥接用 `rmdir "C:\Users\zhugu\.kimi-code\skills"` 即撤（junction，源目录 `~/.kimi/skills` 未动）。
