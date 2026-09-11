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

## 7. 附录：2026-09-11 恢复与复核（config.toml 重放 + 重试码漂移修复 + 双上游探活）

**Status:** 已生效。触发：cc-switch 覆写漂移如期发生（§4 预言），用户指令"继续"授权 a/b/c 三项。

### 7.1 Codex 默认链恢复（重放 §3.2）

- `~/.codex/config.toml` 当日实况：`model_provider="custom"`（15721 Sub2API 断链），`[model_providers.any]` 整块消失。
- 处置：备份 `config.toml.bak-20260911-before-restore-any` 后重放 3.2 节（`model_provider="any"` + any 块 + `newapi_probe_key` 明文注入；custom 块保留作回滚载体）。
- 验证：`codex exec` 默认配置 → provider=any 生效，NewAPI log 6 行 astra 记录（1789119429–1789119454）确认路由经 3002→ch126；**接线正确但上游全失败**（见 7.3）。

### 7.2 重试码漂移修复（§3.3 存量 FAIL 之一）

```text
python3 scripts/ops/update_newapi_retry_budget.py --apply
→ backup=newapi-retry-budget-20260911-174113.json（~/.new-api-local/backups/）
→ AutomaticRetryStatusCodes: 400,408,429,500-503 → 408,500-503（readback verified ok=True）
```

后续冒烟门禁（17:41）：FAIL 9→7（清除 ①漂移项 ②ch87 零输出计费——后者外部自行恢复，非本次改动）；`channel model isolation violations=none`，零新增违规。其余 7 项存量 FAIL 未动（opus 主池 ch3/9/18、ch78 缺失、pool capacity、ch45/92 sol 别名 abilities、sensenova 404）。

### 7.3 双上游探活（密钥经 env_key 注入，未进 argv/config）

| 上游 | 方法 | 结果 |
|---|---|---|
| any（直连 `https://anyrouter.top/v1`，绕过 NewAPI） | codex exec `-c` 覆盖 + env key | 401 排除后复现 **Azure 部署级错误**：`Could not find an existing deployment to match the model`——`gpt-6-astra` 部署已被上游移除/改名；目录 `GET /v1/models` 仍列 15 模型（目录≠可服务，§1 同款教训）。昨日 32% 截断已恶化为 100% 失败。 |
| zzzcoding（直连 `https://api.zzzcoding.org/v1`，绕过 NewAPI） | 同上 | **503 Service temporarily unavailable**（上游自有 request id，非 NewAPI 冒泡）——codex 门面存在但服务不可用；与 §4「sub2api 面 405」不同面。**09-11 复核（17:5x）**：目录仅 `gpt-6-astra`（sol 已下架，探之 404 `not supported by any configured account in this group`——对照证明网关存活、认证通过、路由正常）；astra 503 为上游账号池级不可用，两轮间隔约 1h 同签名复现，非瞬时抖动。 |

- **按 §4 纪律：ch92 探活失败，保持 status=2，不复活**（复活时还须同步改过期 test_model `zzzcoding-codex-gpt-5.6-sol`→`gpt-6-astra`，并决策 priority：p60 复活即成 astra 主路，any 主/zz 兜底则须降 priority<50）。
- **当前态：astra 池内 ch126 仍 status=1（唯一活跃渠，auto_ban=0 不自禁），但其上游部署失效；ch92 禁用。gpt-6-astra 实质无可用源，恢复依赖上游侧修复。**

### 7.4 过程教训

- **密钥经 `-c env_key=VAR` 探活时**：env 值须 `tr -d '\r\n'` 清洗（secrets.json 读取自带尾换行 → 401「未提供令牌」，与 memory「CRLF 授权头污染」同类）；且 bash 多行脚本会被分段执行导致 env 不达子进程，须单行 `&&` 链。
- codex `env_key` 机制本身可用（0.154.0）；排除 401 后错误形态即上游真实错误。

## 8. 2026-09-11 晚间全池 GPT 实弹普查（codex 0.154.0，探活先行纪律）

**结论：池内无任何可用 GPT 源；codex CLI 当晚不可用 GPT。ch126 未动、ch91 探测后已还原，净 DB 变更为零。**

### 8.1 实弹结果（全部真 `codex exec` 直连，key 经 env_key 注入）

| 源 | 模型 | 错误形态（原样） | 判定 |
|---|---|---|---|
| ch126 any（默认链 3002） | gpt-6-astra | `Could not find an existing deployment to match the model in the request`（5/5 Reconnecting） | 上游 Azure 部署被摘 |
| any 直连 | gpt-5-codex | `404 当前 API 不支持所选模型 gpt-5-codex` | 死列表，08-15 起未复活 |
| ch92 zzzcoding | gpt-6-astra | `/v1/responses` 返回 **nginx HTML 错误页**（12.8s，非 JSON） | 门面已死 |
| ch83 muyuan | gpt-5.6-sol | `503 No available channel for model gpt-5.6-sol under group default (distributor)`（上游自有路由器透传） | 上游池空 |
| ch87 ooioo | gpt-5.6-sol | `403 预扣费额度失败, 用户剩余额度: ＄0.000006, 需要预扣费额度: ＄0.018980` | 余额枯竭（≈签到可回血） |
| ch30 fastaitoken | gpt-5.6-sol | `403 INSUFFICIENT_BALANCE` | 余额枯竭 |
| ch62/63/65 centos 全家 | gpt-5.6-sol | `403 用户额度不足, 剩余额度: ¥-0.008314`（三域名同账户同余额） | 单账户余额枯竭 |
| ch70 vip.j3gb | gpt-5.6-sol | `403 INSUFFICIENT_BALANCE` | 余额枯竭 |
| ch82 7758 | gpt-5.6-sol | `401 Invalid token`（目录仍列 gpt-5.4/5.5/5.6-luna/sol/terra） | key 失效 |
| ch91 jianzhile（经 3002，DB 直启 channels+abilities） | gpt-5.6-sol | `503 No available channel for model gpt-5.6-sol under group GPT (distributor)`——stderr 归因 `channel error (channel #91, status code: 503)` = **ch91 上游自己的 GPT 池空** | 上游池空；**已还原 status=2 + abilities enabled=0**（备份 `channel-91-before-probe-20260911-202924.json`，readback 零 diff） |

### 8.2 agentrouter GPT 新路（用户线报"agent渠道加了gpt模型"，已核实 + 受阻）

- 8788 本地 agentrouter-proxy 目录（`GET /v1/models`，client key=ch45）：新增 **`gpt-5.6-sol`、`gpt-6-astra`**（连同 claude-opus-4-8/5、glm-5.3、deepseek-v4-flash 共 6 模型）。
- 直连核实：`https://agentrouter.org/v1` 与镜像 `https://ps.air-outer.com/v1` 同目录，**且 `/v1/responses` 端点真实存在**（402 说明请求已穿透认证与路由，进入计费层）。
- **阻断**：GPT 预算池枯竭——pool keys ×4 + ch86 key 全部 `402 Budget pool quota has been exhausted. Please ask an administrator to increase the limit or select another budget pool.`（gpt-5.6-sol / gpt-6-astra 均 402）。glm-5.3 流不受影响（另一预算池）。
- 门禁细节：两上游有 **UA 门**——python-urllib UA → 401；`claude-cli/2.1.158` 与 `codex_cli_rs` UA 均放行。8788 代理出站带 `claude-cli` UA（`agentrouter-proxy.py` `_headers()`）；codex 实弹若接 agentrouter，需 NewAPI 渠道 header_override 注入 codex UA 或经 8788（8788 仅 `/v1/chat` 无 `/v1/responses`，而本机 codex 0.154.0 已移除 wire_api=chat——`-c wire_api=chat` 报错指向 discussion 7782）。

### 8.3 复活路径（按优先级）

1. **agentrouter GPT 池回血**（基建已就绪：responses 面 + 目录 + UA 门已知解法）→ 用户侧动作：agentrouter.org 面板查看预算池/换池/充值；恢复后新建 NewAPI 渠道（type=openai, base_url=`https://agentrouter.org/v1` 或 ps.air-outer.com, header_override 注入 codex UA, test_model=gpt-5.6-sol, 探活先行），codex 默认链仅改 `model=`。
2. **any 重铺 astra 部署**：ch126 配置原封不动（auto_ban=0），上游恢复即自动可用，无需变更。
3. 公益站回血（ooioo/centos/fastaitoken 签到或充值）→ 渠道探活后启用。

### 8.4 过程教训（本轮新增）

- 本 fork 渠道更新 API `PUT /api/channel/` 对最小体 `{id,status}` 与全量/slim 对象均返回 `Invalid parameters`（与 memory「最小体可用」记录不符——fork 版本行为变化）；**启用/禁用走 DB 直写须同时改 `channels.status` 与 `abilities.enabled`**（abilities 不动则路由池不生效）。
- ch91 上游 503 报文含 `under group GPT`：该 "GPT" 是 **上游 jianzhile 自有分组**，非本地 NewAPI 分组（本地 token/abilities 全为 default）——读上游错误时先归因再动手。

## 9. 2026-09-12 affinity 换绑打断无状态 reasoning 回放（会话变砖 + rollout 修复程序）

**Status:** 会话已修复并实弹验证通过；NewAPI 侧零变更（复用 §7/repair 已生效的 affinity 配置）。

### 9.1 症状与归因

- 症状：会话 `01a090de`（09-11 晚大配置会话）每轮报 `stream disconnected before completion: Item with id 'rs_0434bea8...' not found. Items are not persisted when store is set to false`，重试必现。
- 机制：codex `disable_response_storage=true`（无状态回放），每轮请求携带全部历史 reasoning 项（id + `encrypted_content`）；**加密 reasoning 与产出它的上游账号绑定**——换账号回放即被 OpenAI 拒绝（`encrypted content could not be verified` 或 `Item with id ... not found`）。毒项一旦进入 rollout，该会话对任何新账号永久 400。
- 触发链（NewAPI log 佐证）：00:47 drill 清 affinity 缓存 → 会话从账号 A（`016aa4...` 前缀）换绑账号 B（`06aa4...` 前缀）→ 历史里同时存在 A/B 两账号的 reasoning 项 → 00:53 起报 verify 失败；01:05:34 `repair_codex_sharedchat.py --apply` 再次清缓存 → 换绑 ch128 SharedChat（p60 优先）→ 全部旧 reasoning 项皆外来 → 01:05–01:13 该会话所有请求 ch128 记 0/0「上游没有返回计费信息」即此病；新会话（`16c1f64e`）01:15 也吃到一次 103s 上游挂起。
- 教训：**探活/修复脚本禁止在 codex 会话活跃时清 `channel_affinity_cache`**（TTL 300s 到期重绑同理，依赖 SharedChat 侧经透传的 `Session_id/Thread_id` 头保持其内部账号粘性）。

### 9.2 会话修复程序（可复用）

```text
python3 scripts/ops/codex-resume-scrub.py <rollout.jsonl> --apply
# 备份 <rollout>.bak-reasoning-scrub-<ts> 后剔除全部 response_item/payload.type=reasoning 行
codex exec resume <SESSION_ID> -c sandbox_mode="read-only" "<最小探针>"
```

- 本次：剔除 186 个 reasoning 项（1233→1047 行），备份 `rollout-2026-09-11T22-28-40-...jsonl.bak-reasoning-scrub-20260912-012342`；探针 `codex exec resume 01a090de-... "只回复OK"` → 17.7s 完成，链路 3002→ch128→SharedChat 实弹通过。
- 边界：仅清客户端 rollout 投影；已混杂多账号历史的会话只有此法可救（affinity 救不了存量污染）；TUI 恢复该会话即可继续（resumed 模型若与录制模型不符会有 warning，属预期）。
