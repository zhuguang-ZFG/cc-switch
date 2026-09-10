# any 渠道 GPT 接入 Codex——ch126 + 默认供应商切换（2026-09-10）

**Status:** 已生效（Codex 默认配置实弹验证通过）
**Scope:** NewAPI 渠道 ch126、`~/.codex/config.toml` 默认 provider 切换。未触碰 cc-switch 本体、8789 桥、OMP 配置、ch72/ch92。

## 1. 背景与触发

- 用户指令：把 any（anyrouter.top）渠道的 GPT 模型配置到 Codex。
- 现场证据：当前 Codex 默认链（15721 → cc-switch 现役 provider `Sub2API` → 上游）**已断**——`codex exec` 默认配置报 `405 Method Not Allowed`（nginx），重试 5/5 全挂。切换 any 属修复而非增强。
- any 目录（经 8789 桥 `GET /v1/models`，2026-09-10）：15 个模型。真实可用的 GPT 模型只有 **gpt-6-astra**；`gpt-5-codex` 为死列表（上游 404「当前 API 不支持所选模型」，与 2026-08-15 窗口调查一致），**未配置，避免静默失败**；`gemini-2.5-pro` 非 GPT，出范围。

## 2. 上游门禁结论（实测）

- 上游 codex 通道对**合成请求**拒绝：`POST 8789/v1/responses {model:gpt-6-astra, input:string, stream:false}` → 400 `invalid codex request (invalid_responses_request)`——与 2026-09-06 结论一致。
- **真 Codex CLI 请求原生可过**：`codex exec`（0.153.4）直连 `https://anyrouter.top/v1` + 明文 Bearer（any key）→ `ANY_CODEX_OK`，8.4s / 12,172 tokens。门禁只验 body 形状，不要求 codex 指纹头。
- NewAPI 3002 透传真 Codex 请求到 type-1 渠道**无需** ch91 式 param_override/header_override（jianzhile 需要头注入是其自身门禁，any 不需要）。

## 3. 变更

### 3.1 NewAPI 渠道 ch126 `any-gpt-6-astra`

`POST /api/channel/`（mode single）：

| 字段 | 值 |
|---|---|
| type | 1（OpenAI） |
| base_url | `https://anyrouter.top` |
| key | any key（`~/.omp/guardian/secrets.json` 的 `anyrouter_proxy_key`，未落仓库） |
| models | `gpt-6-astra` |
| group / priority / weight | default / 50 / 5 |
| auto_ban / status | 0（公益站挤窗语义，同 ch45/ch72 先例）/ 1 |
| test_model | `gpt-6-astra`（ch72 故障域拆分教训：必填） |
| model_mapping / param_override / header_override | 空（实测不需要） |

- **管理端测试是假阴性**：`GET /api/channel/test/126?model=gpt-6-astra` → 404「当前 API 不支持所选模型」——管理测试走合成 chat 面请求，被上游 codex 门拒，与真 Codex 路径无关。ch126 的可用性以 Codex 实弹为准。
- 未动 ModelRatio：沿用 ch92 时代现状（fallback 计价，12k-token 请求 quota≈571341）。如需对齐 bai-free 式免费置 0，另起变更（注意 ratio 是模型全局，会影响将来复活的 ch92 计费显示）。
- ch72（Anthropic 故障域，仅 Claude）与 ch92（zzzcoding，status=2 未动）保持原状；astra 聚合池当前唯一活跃渠 = ch126。

### 3.2 Codex 默认供应商切换（`~/.codex/config.toml`）

```toml
model_provider = "any"          # 原 "custom"（cc-switch 15721，已断）
model = "gpt-6-astra"           # 不变
model_reasoning_effort = "high" # 不变
disable_response_storage = true # 不变

[model_providers.any]
name = "Any-GPT"
base_url = "http://127.0.0.1:3002/v1"   # 经 NewAPI：usage 计费入账 + 聚合池 failover
wire_api = "responses"
experimental_bearer_token = "<newapi 客户端 key 明文，51 字符>"  # 文件内已有同类明文先例（GitHub PAT）
```

- `[model_providers.custom]`（cc-switch 15721）块**保留**作回滚载体。
- 备份：`~/.codex/config.toml.bak-20260910-any-cutover`（切换前完整副本）。

### 3.3 验证

| 步骤 | 证据 |
|---|---|
| 直连上游 | `ANY_CODEX_OK`，8.4s（codex exec + `-c` 覆盖 → https://anyrouter.top/v1） |
| 经 NewAPI 3002 | `ANY_NEWAPI_OK`，6.9s（codex exec + `-c` 覆盖 → 3002，ch126） |
| 消费归因 | NewAPI log：`channel=126, model=gpt-6-astra, is_stream=true, use_time=5s` |
| 默认配置实弹 | `ANY_CUTOVER_OK`，6.7s（无任何 `-c` 覆盖，走 config.toml） |
| 仓库门禁 | `newapi-local-smoke.py` 手动跑（19:36）：`channel model isolation — violations=none`；unexpected_disabled 不含 126 |

门禁当轮 **9 个存量 FAIL 有直接前置证据**：`.tmp-newapi-dx-ops.log` 中 19:25:01 定时冒烟（ch126 创建前，`total=64 enabled=21`）与 15:25:01 两轮摘要与 19:36（`total=65 enabled=22`，+ch126）完全一致——ch126 零新增违规。9 项：opus 主池 ch3/ch9/ch18 被禁与容量 1<2、ch78 缺失、`AutomaticRetryStatusCodes=400,408,429,500-503` 漂移、ch87 零输出计费、ch45/ch92 abilities 缺失（ch92 模型槽 09-05 改 astra 所致）、sensenova-6.7-flash-lite 404。**未做批处理修复**（保守变更纪律），需另行立项。

## 4. 风险与边界

- **cc-switch 改写覆盖**：config.toml 是 cc-switch codex 供应商投影；用户下次在 cc-switch 里切 Codex 供应商会整体覆写本文件（any 块消失，回到断链的 custom）。恢复方法：重放本文件 3.2 节（或恢复 bak）。cc-switch 本体在禁区，无法从根上消除此漂移。
- any 上游仍有负载上限窗口（500「负载已经达到上限」/429 拥堵式拒绝）；**astra 当前唯一活跃源 = ch126**——ch92（zzzcoding）2026-09-05 晚被手动禁用（other_info `status_reason=manual operation`，status_time=1788608094），且近 14 天零消费记录、最近管理测试停在 08-19。any 撞负载窗口期间 astra 无 failover 兄弟；复活 ch92 前必须先探活其上游（zzzcoding 同域 sub2api 面 09-10 实测 405），禁止未探活直接 status=1。
- `experimental_bearer_token` 明文 key 与文件既有明文先例一致；如轮换 NewAPI 客户端 key，需同步改本行。
- gpt-5-codex / gemini-2.5-pro 故意不配置：上游 404 死列表，配置即静默失败。
- **OMP 不可用（2026-09-10 实测+结构）**：上游 Codex-only 门仅认真 Codex CLI 请求——6 次手搓 `/v1/responses`（minimal / string-input / codex 指纹头 / codex 形 body 含 tools+reasoning+include）经 3002 全 400 `invalid codex request`，仅真 Codex CLI 过门。OMP 无 instructions/header/body 覆写能力（models.yml 仅 api 类型切换；extensions 仅 4 个路由/守护钩子，无请求中间件），同型门 09-05 zzzcoding 会话已实测「OMP 全过不了」。结论：gpt-6-astra 仅供 Codex CLI，OMP 不建条目；若要 OMP 使用需自建 codex 指纹转换桥（项目级工作，门禁漂移风险高，未立项）。
- **codex CLI 环境事件与修复（当晚 19:47–20:15）**：19:47 起 codex.CMD 报「系统找不到指定的路径」。根因 = **重装竞争窗口**：用户的 codex/agent 会话当晚活跃地用 npm 管理 codex（npm 日志 `_logs` 12:05/12:07/12:12/12:13Z，12:07:45Z 的 cwd 为 `D:\temp\User\tmp.a1iX3uGOgq` 非运维临时目录；12:12:29Z 无版本钉安装 0.154.0，期间 EPERM 触碰运行中 codex.exe，最终干净完成、平台包**嵌套放置**正确），重装窗口内文件被换出/瞬时残缺。诊断中两次 `npm i -g`（~11:5xZ）未留 debug 日志、sibling 探测未见平台包——但平台包实为嵌套布局 `codex/node_modules/@openai/codex-win32-x64`，sibling-only 探测结构性失明；**「npm 跳过别名列可选依赖」结论证据不足，撤回**。临时以 `npm pack` 手动解压 0.153.4 平台包到平铺兄弟位支撑过验证；20:12 后树自洽 0.154.0，手动副本已撤。教训：平台包存在性探测须查嵌套位 `vendor/x86_64-pc-windows-msvc/bin/codex.exe`（包无顶层 `bin/`）；勿与用户活跃安装抢树，0.154.0 为准。验证：node 直跑 / python subprocess / cmd `/d /s /c` 三路 `--version` 全通；git-bash 直调 .CMD 报路径错误属 MSYS 执行层怪癖，不影响用户原生终端。20:15 端到端复测：0.154.0 下 `codex exec` 经默认链完整跑通（74s，模型回复正常打印，codex 计 27,141 tok）；ch126 的 NewAPI 记账行在成功窗口也标 `stream: error`（usage 尾帧上游丢失，不影响内容流）。
- **any 上游间歇断流（当晚在发生）**：ch126 当晚 8 行消费中 4 行异常（`上游没有返回计费信息（可能是上游超时）`，含用户 110k-token 真实会话 2 次中断 19:49–19:50、回归请求后 2 次重试 20:09:18/44 失败）——codex 表现为 `error: interrupted / Reconnecting 1/5`。属 any 负载窗口的流中断形态，非本地问题；持续恶化则触发 §4 的 ch92 复活预案（先探活上游）。

## 5. 回滚

```text
~/.codex/config.toml.bak-20260910-any-cutover   # 恢复 Codex 默认链（注意：该链本身 405 断）
ch126: PUT /api/channel/ status=2 或直接删除     # 摘除 NewAPI 侧
```

## 6. 附录：Codex TUI 进程静默退出调查（当晚 19:40–20:45）

**Status:** 结论收敛，触发者待退出码裁决。用户症状：TUI 会话运行几分钟后自行消失、无错误提示。

### 6.1 进程普查（`~/.codex/logs_2.sqlite`，今日 7 次启动）

| pid | 存活 | 日志行 | 性质 |
|---|---|---|---|
| 8148 | 19:40:09→19:44:55 | 1159 | 真实会话，中途消失（无任何收尾行） |
| 14208 | 19:48:37→19:50:33 | 1063 | 真实会话，中途消失 |
| 6840 | 19:51:04→19:51:08 | **3** | 启动 4 秒即退：仅公告拉取 + OTEL flush，未进会话 |
| 17052 | 20:12:22→20:12:29 | **4** | 启动 7 秒即退，末行 `failed to finish interactive telemetry shutdown (exceeded time budget)` |
| 12292 | 20:12:41→20:19:00 | 1248 | 真实会话，死在 commandExecution 流中间 |
| 22848 | 20:19:59→20:27:05 | 1185 | 真实会话，死在重试 2/5 中间 |
| 17848 | 20:27:27→20:41+ | 1264+ | **存活**，期间经历同样错误风暴（13 次重试） |

死进程全部**没有** `Shutting down Codex instance` 行——未走任何关闭路径。turn 层：thread 01a08b3c 的全部 turn 永久 `inProgress`（17 个悬挂 turn，`codex resume` 可能恢复不回，#37754）。

### 6.2 已排除项（本地证据）

- **非崩溃**：48h 事件日志无 codex 的 Application Error/Hang；`%LOCALAPPDATA%\CrashDumps` 空；WER ReportQueue/Archive 空；日志库无 panic。
- **非 OMP/harness 杀**：codex 挂在普通 PowerShell 标签（WT，cwd `D:\Users\hutuji`）→ node npm shim → codex.exe；`~/.new-api-local/watchdog.ps1` 只保 new-api/guardian/supervisor，不碰 codex。
- **非网关重启**：本地 new-api（PID 17056）12:55 启动至今未重启。
- **非更新瞬间**：0.153.4→0.154.0 自动升级在 20:12–20:14，但 19:40/19:48 的死亡在其之前。
- **非 `--disable tui_app_server` 可救**：0.154 `codex features list` 显示 `tui_app_server  removed  true`，开关已移除。

### 6.3 turn 级归因：上游风暴不杀进程

| 进程 | 临死时重试的 turn | 重试次数 | turn 起始 rollout 字节 |
|---|---|---|---|
| 12292（死） | `4cd6`（5/5 前） | 5 | 22 KB（最小） |
| 22848（死） | `1629`（3/5） | 3 | 1.96 MB |
| 17848（**存活**） | `d857` ×8 + `df32` ×5 | **13** | **2.71 MB（最大）** |

存活者跑在最大 turn、扛最多重试仍活着 → 死亡与具体请求/体积/重试次数**无相关**。上游问题是并发的病（见 6.4），不是杀手。

### 6.4 上游实况（当日全量，补全 §4 的抽样窗口）

- 中转侧（new-api.db，token `local-windows-clients`，channel 126 anyrouter.top）：gpt-6-astra **62 次请求 / 20 次被上游截断（32%）**，日志 `上游没有返回计费信息，无法扣费（可能是上游超时）`；与 §4 当晚抽样（8 行中 4 行异常）一致，此处为全日补全。
- codex 侧今日：`stream disconnected` 重试告警 26、限流相关 27（其中 `rate limit exceeded … eastus2` 14）、`high demand` 13。
- turn 结局：`completed 119 / failed 89 / inProgress 17 / interrupted 5`。
- ch92（zzzcoding，astra 备用渠道）仍禁用；复活前须先探活其上游（§4 约定不变）。

### 6.5 判定与待办

**判定（ranked）**：进程被终止时未走任何关闭路径、无崩溃记录 → ① 外部/控制台级终止（Ctrl+C×2、TerminateProcess、控制台关闭）或 ② 0.154 TUI/App-Server 静默自退（与 Windows 静默退出家族 #40576 签名一致：末行正常→无声消失，无 WER 无 dump）。上游断流列为并发问题，已排除为杀手。

**待办（一次裁决）**：
```powershell
codex; "exit=$LASTEXITCODE"
# 3221225786 (0xC000013A) → 外部终结 | 101 → panic | 0/1 → 自身退出
```
隔离试验：同终端 `codex exec "ping"` 循环 10 分钟，exec 活得比 TUI 久 → TUI 层问题。若为 0xC000013A 需 ETW/ProcMon 追终止者。

**顺带卫生**：`C:\Users\zhugu\.cc-switch\skills\ecc\SKILL.md` 缺 YAML frontmatter（每次启动报 ERROR）；`~/.codex` 175 个 rollout 共 506MB、`logs_2.sqlite` 277MB、`thread_history_1.sqlite` 121MB 可清；17 个悬挂 `inProgress` turn 待归档。
