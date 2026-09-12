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

