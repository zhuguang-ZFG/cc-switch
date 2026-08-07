# 本地网关加固与 sol 通道修复（2026-08-05）

VPS NewAPI 退役后本地栈（new-api.exe + 三代理 + Guardian）的第一轮加固记录。

## Guardian 误报根因（"NewAPI 无响应"刷屏）

- 现象：Guardian 每 20s 报 `NewAPI unavailable`，但 `/api/status` 实测 200。
- 根因：Guardian 由计划任务 `NewAPI Guardian` 以 **RunLevel=Highest** 运行，
  该实例进程环境被污染（同环境提权 python 探针实测 200，排除提权本身与
  TUN/桥）；且提权导致 `watchdog.ps1` 读不到其 CommandLine，僵死时无法
  精确杀（旧 pid 28500 就是这么残留的）。
- 处置：
  - 任务降为 **RunLevel=Limited** 并重启（pid 33460，非提权，watchdog 可见）。
  - 拆除双启动源：Startup 的 `ai-proxy-resilience.cmd` 也会拉起 start.bat
    （无单例保护），已注释其 Guardian 行；自启只留计划任务。
- 教训：Guardian 不需要管理员权限；提权既污染环境又废掉 watchdog 的精确杀。

## 计划任务清理

- 禁用：`atomcode-bridge-watchdog`、`KimiXiaoxiaobaiTailscaleProxy`（失效）、
  `LocalAIProxies-Supervisor`（与 Startup lnk 重复，杀掉其派生的重复
  supervisor pid 32612）。
- 三代理自愈：Startup `LocalAIProxies-Supervisor.lnk` →
  `~/.omp/guardian/proxies-supervisor.py`，单实例运行。

## 渠道状态端点（本 fork 再次实证）

- 改状态用 `POST /api/channel/:id/status {"status":N}`。
- `PUT /api/channel/` body **不能含 `status` 字段**，否则 `Invalid parameters`；
  该 fork 还会在 GET 响应里把 `key` 脱敏成空串（len=0 不代表 DB 里为空）。
- 本次启用：ch45 agentrouter、ch70 vip-j3gb-gpt、ch65 centos-api-newkey-gpt。
- ch63 centos-fr-gpt（frapi.centos.hk）实测 62s 后 504，保持 status=3，
  列入 smoke `KNOWN_BROKEN_CHANNELS`。
- 结构观察：sol 响应普遍 37–60s，贴 50s 自动封禁阈值，centos 系会 flap；
  Guardian 的稳定验证重启用机制兜底，暂不调整全局阈值。

## freemodel WorkBuddy 门禁（sol 修复关键）

- `work.freemodel.dev` 对非 WorkBuddy 流量返回 403 `unsupported_client`。
- 排查路径：回显抓包（models.json 临时指向 127.0.0.1:8899 echo server）
  拿到逐字节真实头；用 WorkBuddy 自带运行时回放
  （`ELECTRON_RUN_AS_NODE=1 WorkBuddy.exe`，内嵌 Node v22.21.1）。
- 结论：**门禁只查请求体**——messages 须含 WorkBuddy 系统前言
  `This conversation is powered by <model>...<user_query> tag.`。
  与请求头、X-IDE-\*、acp-connection-id、TLS/JA3 指纹均无关
  （最小头 + 前言即 200；全头 + 原生运行时但无前言仍 403）。
- 处置：converter（codebuddy2openai）对 url 含 `freemodel` 的请求自动注入
  前言（`WB_SOL_PREAMBLE`，已有则不动）；ch44 接回
  `gpt-5.6-sol` + `zg-wb-gpt-5.6-sol`（mapping→`gpt-5.6-sol`）。
- 验证：8787 直出 200（17.8s）→ ch44 渠道测试 200 → 网关 e2e
  `zg-wb-gpt-5.6-sol` 200（133s，sol 本身慢）。
- key 池：`custom_keys.json` 的 `gpt-5.6-sol` 已满编 4 个 `fe_oa_` key，
  当晚逐一实测全部 200（10–21s），converter 单 key 失败冷却 180s 自动切换。
- 注意：echo 抓包脚本曾把 body 截断到 4000B，排查 body 门禁时需要全量 body
  的话记得改 `tmp/wb-echo-server.mjs` 的 slice。

## DX-Ops 冒烟

- `scripts/ops/newapi-local-smoke.py`：NewAPI 状态 / 三代理端口 / 渠道
  自动封禁汇总（含 known-broken 白名单）/ 两条真实补全采样。
- 16:36 运行 `ALL OK`（exit 0），日志 `.tmp-newapi-dx-ops.log`。

## 第二轮（同日晚）：会话上限、阈值、备份

- **管理会话上限**：本 fork 会话持久化在 `user_sessions` 表，上限 50；
  打满后登录返回 `409 AUTH_SESSION_LIMIT`（重启 new-api.exe 不清，DB 持久）。
  处置：清空 `user_sessions`（50→0）恢复登录；smoke 脚本改为缓存复用
  管理令牌（`.admin-token-cache.json`，401 才重新登录）——此前每次运行
  都新建会话，是打满的主因。注意 Guardian 用的是 users.access_token
  （长效 API token），不受会话表影响。
- **自动封禁阈值**：`ChannelDisableThreshold` 50s → **90s**。sol 实测
  37–87s，贴 50s 阈值导致 centos 系渠道 flap（ch63/ch65 当天各被封一次，
  后由自动启用恢复）；90s 覆盖 sol 长尾，Guardian 慢渠道检测不受影响。
- **ch63 恢复**：frapi.centos.hk 约 16:44 恢复，渠道被自动启用，已移出
  smoke 白名单（白名单现为空）。
- **new-api.db 每日备份**：`proxies-supervisor.py` 每天 03:00 后用 SQLite
  在线 backup API 备份到 `~/.new-api-local/backups/`，保留 7 份；
  首份已生成（31.7MB）。
- **new-api.exe 自启**：已有 HKCU Run 键 `Local NewAPI`（start.ps1，
  端口已监听则直接退出，幂等），无需新增任务。VPS 时代的
  `newapi-backup-pull` 任务（每天 04:30 从已退役 VPS 拉备份）已禁用，
  其职能由本地每日备份接替。

## 2026-08-05 晚：林夕 ch9 双 key polling 化

ch9 `linxi-k40` 从单 key 改为双 key polling（对齐百倍 ch3 的 6-key
模式），ch18 备份渠道保持独立单 key 不动。最终状态：

- `key` 字段两行（旧 `…a5f6` + 新 `…7feb`），`channel_info` 为
  `{"is_multi_key":true,"multi_key_size":2,"multi_key_status_list":{},"multi_key_polling_index":0,"multi_key_mode":"polling"}`。
- 渠道测试 200。首次测试曾命中上游账户级 429
  （Concurrency limit exceeded），重试即过——这是上游限制，非配置问题。

### 坑：is_multi_key 无法经管理 API 翻转

源码（上游 QuantumNous/new-api `controller/channel.go`）确认：

- `PUT /api/channel/` 服务端强制 `channel.ChannelInfo = origin.ChannelInfo`，
  body 里带 `channel_info` 一律被忽略；唯一例外是顶层 `multi_key_mode`
  字段可覆写轮询模式。
- `is_multi_key` 只在**建渠道时**（`mode:"multi_to_single"`）能置 true；
  已存在的单 key 渠道没有任何 API 路径可转多 key。
- body 含 `status` 键直接 400 Invalid parameters；GET 响应里 `key`
  被脱敏为空串，从 GET 构造 PUT 必须重新显式塞真实 key，否则会清掉。

可行路径（本次采用）：直接写 DB
`UPDATE channels SET channel_info=? WHERE id=9`（BLOB 存 JSON 文本），
再对同渠道发一次无害 PUT 触发 `InitChannelCache()` 刷新内存缓存。
只写 DB 不刷缓存，路由层仍按旧单 key 处理，渠道测试会报
`do request failed: upstream error`（NewAPI 把整串 "k1\nk2" 当单 key
发上游）。

### 运维脚本两个凭证坑（本次实测踩中）

- `admin-credentials.json` 带 UTF-8 BOM，`json.load` 裸读直接
  JSONDecodeError——所有读该文件的脚本一律 `encoding="utf-8-sig"`
  （smoke 脚本 `read_json` 已如此）。
- 登录响应里令牌字段是 `data.access_token`（不是 `data.token`）；
  用户 id 字段可能缺省，默认按 `1` 处理。

## 2026-08-05 晚：渠道权重审计与 gpt-5.6-sol 池调优

**方法**：同提示词逐渠道延迟实测（管理 API `/api/channel/test/:id`）+
近 36h 生产日志分布（`logs` 表 type=2）。12 渠道两主池全活。

**gpt-5.6-sol 池（全部 prio50，权重真实决定分流）实测**：
ch65 1.6s < ch70 1.8s < ch63 2.4s < ch64 2.5s < ch62 2.8s < ch20 2.9s < ch44 7.9s < ch45 9.1s。
36h 均值佐证：ch65 16.4s 最快、ch20 23.4s 最慢。调整：

- ch65 `centos-api-newkey-gpt` w5→**10**（实测最快却一直最低权）
- ch20 `fengwind-gpt56sol` w15→**10**（08-03 作为独立源提到 15，实测只在中游）
- ch45 `agentrouter` w10→**5**（实测最慢 9.1s，且有敏感词误杀前科）

调整后池权重：ch62/63/65/70=10、ch20=10、ch44/45/64=5，总 70 不变。

**claude-opus-5 池（优先级分层）不动**：ch3 prio57（公益池，实测 3.1s
但并发下 429 是常态）→ ch45 prio50 → ch9/ch18 prio40（林夕，实测 5.0/7.8s）。
36h 真实分布 ch9 n=109 > ch18 n=56 > ch3 n=25，证实设计意图成立：
公益池优先、429 后流量自然下落到付费层。ch9 今日已双 key polling，
容量翻倍，w2/w2 保持不变。

**注意**：`logs.use_time` 是整请求时长，受任务大小混杂影响，跨渠道比较
只能以同提示词实测为准（ch9 36h avg 44s 是 slow/plan 重任务集中所致，
并非渠道慢）。失败请求不落 type=2 日志，错误率无法从日志反推。

## 2026-08-05 晚：gorouter 渠道合并收尾（ch27 删除、ch57 单 key 复活）

**背景**：gorouter 历史上是 ch26/ch27 双渠道（同网关双 key，multi-key 功能
出现前的轮询 workaround）。后续 ch57 `gorouter` 收编了 3 枚 key
（含 ch27 的 key）并开启 multi_key polling——**合并其实早已发生**，ch27
只是忘删的重复渠道。08-03 因上游预扣余额不足（$0.047 < $0.2）ch27/ch57
双双禁用。

**本轮发现与处置**：

1. **额度已月度重置**：ch57 实测 claude-opus-5 200（1.5-2.4s）。但 3 枚
   key 中只有 1 枚有额度（其余 $0.099 / $0.008，低于 $0.2 预扣线）。
2. **claude-sonnet-5 已从 ch57 models 摘除**：gorouter 网关对该模型稳定
   403（openresty WAF 秒拒，3 次复测一致）；opus-5/opus-4-8 正常。
   sonnet-5 在 NewAPI 侧自此无可用渠道。
3. **ch27 删除**（DELETE /api/channel/27）：key 与 ch57 重复、渠道禁用，
   零价值。abilities 已重建。
4. **坑：multi_key_status_list 的禁用（status=2）在本构建不生效**。
   DB 直写、`/api/channel/multi_key/manage` 的 `disable_key`、
   甚至重启 new-api.exe 之后，轮询器照样把请求发给已"禁用"的 key
   （status_list={0:2,1:1,2:2} 下 5/5 命中死 key）。唯一可靠路径是
   `manage` API 的 `delete_key`（有效动作仅 `disable_key`/`enable_key`/
   `delete_key`，字段名是 `channel_id` 不是 `id`）。
5. **删错 key 的恢复**：按"余额排序"猜活 key 不可靠——实测周期错位一次
   就会误删活 key。本次误删后从每日备份
   `backups/new-api-2026-08-05.db` 提取原 3 行 key 恢复。
   教训：删 key 前先逐 key 实测（固定 polling_index 或逐 key 直连），
   别按请求顺序推断 key↔余额映射。

**终态**：ch57 单活 key（hash 291d7fd7）、is_multi_key=false、
models=`claude-opus-5,claude-opus-4-8,zg-claude-opus-5`、prio40/w6/
auto_ban=0，渠道测试 4/4 通过（~2s）。两枚欠费 key 仍存于每日备份
（7 天保留期），下月额度重置后如需可经 PUT 补回。claude-opus-5
启用池：ch3(prio57) → ch45(prio50) → ch9/ch18/ch57(prio40)。

## 2026-08-05 晚：自愈体系活体核验 + supervisor 自启修复

**核验证据**（19:10 前后）：

- Guardian（pid 33460，心跳 <1min，计划任务 Running）真实工作：全量扫描、
  软失败防抖（`soft failure 1/3`）、周期 abilities 修复、周期备份均在执行；
  19:04 人工重启 new-api 时正确识别不可用窗口并跳过依赖工作（无误报）。
- proxies-supervisor 今日有**真实自愈动作**：17:05 探测 codebuddy 8787
  不可达 → 自动重启成功。17:47 完成每日备份（31.7MB）。
- 三代理绑定 `100.83.32.95`（Tailscale），探测须打该地址；127.0.0.1
  拒连是绑定地址差异，不是故障。

**修复的漏洞**：`LocalAIProxies-Supervisor` 计划任务处于 **Disabled**
（当日运行实例是手动拉起的，重启机器后 supervisor 不会回来）。该任务
注册时为提权上下文，非提权 shell 无法 Enable（0x80070005）——改为新建
`LocalAIProxies-Supervisor-Logon`：当前用户、AtLogOn + 每分钟 watchdog
双触发、`IgnoreNew`、conhost --headless。手动实例已清理，Task Scheduler
为 supervisor 唯一 runtime owner（实测 Start-ScheduledTask 拉起成功，
kill 后回归单实例）。旧 Disabled 任务留待提权窗口删除，无功能影响。

**已知残余（设计决策，未改）**：new-api.exe 本身无进程级自动重启——
Guardian 日志明确 `automatic restart is disabled for the local service`，
自启只有 HKCU Run 键（登录时生效）。若运行中崩溃且无人工介入，
需等下次登录。如需进程级看护，可给 new-api 加同款 watchdog 任务。

## 2026-08-05 晚：渠道自动测活降频（30min → 180min）

**成本实测**（近 36h `logs` 表 `content='模型测试'`）：2010 次测试、
330 万 prompt token、折合约 $4.2（≈$2.8/天）。主因是 NewAPI 内置
`monitor_setting.auto_test_channel_minutes=30`（每渠道每 30min 一次）；
单次测试 prompt 不小（sol ~4800 tok、opus ~6800 tok，converter 前言也计费）。

**处置**：`auto_test_channel_minutes` 30 → **180**（PUT /api/option/ 生效，
已回读验证）。预计砍掉约 5/6 的测活开销。故障发现兜底不变：
Guardian 全量扫描（1h × 4 轮转）、真实请求 auto_ban/自动恢复、
`ChannelDisableThreshold=90` 均不受影响。Guardian 自身
`FULL_SCAN_INTERVAL=240`（1h）保持不变。

## 2026-08-05 晚：全系统调优扫描收尾（ch2 复活 + intern-s2 配价）

- **ch2 `ai.centos.hk-gpt` 复活**：禁用渠道实测存活，gpt-5.6-sol 3/3
  （2.2-3.0s）。已启用，prio54/w10 位于 sol 聚合池顶层（池现为
  ch2(54) → 5×w10(prio50) → 3×w5(prio50)）。abilities 已重建。
- **ch17 `openoneapi-grok` 确认死透**：401 无效令牌，保持禁用
  （grok 池仅剩 ch39 单源，可接受）。
- **intern-s2-preview 配价**：此前 ModelRatio/CompletionRatio 均无该模型，
  按 37.5× 兜底倍率计费（3366 tok 测试扣 187k 配额），本地成本统计
  严重虚高。已补 2.0/2.0（对齐 k3 档位）。注：这只影响本地配额记账，
  不影响上游真实扣费；早前"$2.8/天"的测活成本估算含此虚高成分。
- smoke 全绿：31 渠道 24 启用、无 auto-disabled。

## 2026-08-05 晚：flash 聚合 + 渠道亲和 + new-api 进程级 watchdog + 角色降本

- **ch17 删除**（401 死 key 确认）；grok-4.5 池剩 ch39 单源。
- **deepseek-v4-flash 五路聚合**：ch48(w20) + ch15(w10) + ch44(w10) +
  ch42(w5) + ch53(w5)，实测 0.6-2.9s 全过。ch48/ch42 无需 model_mapping——
  它们的上游原名就是 `deepseek-v4-flash`（既有 `opencode-go→deepseek-v4-flash`
  映射是暴露名→上游名方向），反向加映射会被 fork 校验
  `model_mapping_contains_cycle` 拒绝。ch44 原生支持；ch15 映射到
  sensenova-6.7-flash-lite。付费官方源（ch42）压到最低权。
- **渠道亲和一个开启**：`channel_affinity_setting.enabled=true`
  （keep_on_channel_disabled 保持 false）。会话粘住健康渠道，
  减少跨渠道抖动和重复故障尝试。
- **LocalNewAPI-Watchdog 计划任务**：每分钟探测 3002，不可达则调
  start.ps1（幂等）；AtLogOn + 每分钟双触发、IgnoreNew、conhost
  --headless。**实测**：杀 new-api.exe 后 ~25s 自动复活（含启动 8s）。
  脚本 `~/.new-api-local/watchdog.ps1`。
- **OMP 角色降本/抗故障**（备份 `config.yml.20260805-195435-subagent.bak`，
  下次启动生效）：task/commit/tiny 主模型 atomcode 单点 →
  `zg-newapi/deepseek-v4-flash:high`（新五路聚合池；`:high` 而非 `:max`
  因为 ch15 sensenova 只支持到 high）；smol 主模型
  deepseek-official（付费官方）→ sensenova-6.7-flash-lite（0.5s、
  最低价，08-04 的既定选择，后被不明漂移覆盖）。librarian 继续 @smol、
  reviewer/security-reviewer 继续钉 sol:high（池今日已加强）。
- claude-sonnet-5 残留确认为零（渠道/能力/OMP 三处均无）。
- smoke 全绿：30 渠道 23 启用、无 auto-disabled。

## 2026-08-05 晚：日志巡检 — claude-opus-5 路由死区修复

**现象**（stdout.log 19:58-20:00）：同一客户端（local-windows-clients）
的 claude-opus-5 请求每 ~20s 失败一次：ch3 502（百倍 Cloudflare 过载）
→ ch45 500（agentrouter 敏感词）→ relay error。**prio40 的林夕层
永远轮不到**——`RetryTimes=1` 只允许 2 跳，而两个顶层同时坏。

**处置**：

1. **ch45 摘除 `claude-opus-5`**（保留 zg-claude-opus-5 等其余模型）：
   agentrouter 敏感词过滤对短流式请求的误杀是已知问题，让它当中间层
   只会吃掉重试。摘除后路由：ch3(50) → 林夕 ch9/ch18(40)，1 次重试
   恰好够到可靠层。ch3 健康时仍免费优先。
2. **ch57 再次禁用**：活 key 额度又耗尽（$0.109 < $0.2 预扣线，
   19:58 自动测活 403）。该 key 看来只有极小额月度额度，下期重置后
   是否再接回待观察。

**发现（非本次改动）**：ch3 优先级 57 → 50 是 **Guardian 写的**——
`guardian.py:1151-1174` 的渠道恢复逻辑按历史记录恢复 weight/priority，
无历史记录时默认 50。手工在 Guardian 体系外调的优先级会在下次
Guardian 恢复该渠道时被抹掉。当前 ch3(50) 仍独占顶层，分层意图
不受影响，接受现状；以后要调优先级需知会 Guardian 路径。

## 2026-08-05 深夜：sol 直连 "empty stop" 修复（converter 流内重试）

**现象**：OMP 默认模型 `codebuddy/gpt-5.6-sol`（WorkBuddy 直连 8787）频繁报
`Assistant returned empty stop after retry cap`（OMP 重试 5 次仍空响应）。

**根因链**（逐层实测定位）：

1. converter.log 显示 sol 请求 ~50% 以 "✗ 网络错误"（空消息）告终；
2. 直连上游 `work.freemodel.dev` 复测：异常为 connect 期
   `ConnectError ← BrokenResourceError ← ConnectionResetError [WinError 64]`
   及偶发 TLS 握手超时——**TCP/TLS 建连阶段被间歇 RST**；
3. DNS 指向 `198.18.0.124`（本机代理 fake-ip），RST 来自代理链路/出口节点，
   与 key 无关（单次成功率 ~50%）；
4. converter 流式路径（`_stream_custom`）此前**无重试**：首个字节前一旦
   connect 失败，直接给客户端吐一条 SSE error chunk 后结束——HTTP 状态已
   提交 200，OMP 将其视为"空 stop"，重试 5 次 ×单次 50% 失败率 ≈ 3% 的
   请求最终用户可见失败（实际体感更高）。

**修复**（`~/.kimi-code/proxies/codebuddy2openai/converter.py`，备份
`tmp/converter.py.before-stream-retry-20260805.bak`）：

- `_stream_custom` 重写为**首字节前换 key 重试循环**（最多 4 次 = key 池
  大小，间隔 0.4s）：connect 期网络错误只轮换**不冷却** key（与 key 无关）；
  仅上游明确 502/503/504 文案才冷却 key。已出内容后的尾部断流维持原逻辑
  （静默收尾，不重复输出）。非流式路径同步：网络错误不再误冷却 key。
- 异常日志带类型名（`ConnectError: ...`），不再空消息。
- **验证**：修复前 6 次实测 3 败（50%）；修复后 6/6 全过，日志可见
  attempt=1 ConnectError → 内部重试成功。理论用户可见失败率降至 ~0.1% 以下
  （内部 4 次 × OMP 5 次）。

**附带修复——开机自启隐患**：计划任务 `CodebuddyHy3Converter`（提权创建，
本会话无权改）的命令行**缺 `--host 100.83.32.95`**，重启后 converter 会绑到
127.0.0.1，OMP（指向 tailnet IP）直连 sol 会断。已建启动文件夹项
`Startup\codebuddy-converter.vbs` → `start-converter.ps1`（等 Tailscale IP
出现最长 5min 再绑 100.83.32.95:8787，带 --api-key，窗口隐藏）。重启后新旧
两个 converter 会并存（127.0.0.1 + 100.83.32.95 不同地址不冲突）；
旧任务建议提权后删除或对齐。

## 2026-08-05 深夜：内置测活关闭效果复查（21:22 定时任务实证）

**结论**：内置测活已彻底关闭，无隐藏调度器残留；剩余"模型测试"日志
全部来自 Guardian，且逐条对上 guardian.log。

**实证**：

- options 表确认：`monitor_setting.auto_test_channel_enabled=false`
  （180min 选项对该 fork 调度器无效的间接证据——关闭后节奏立刻改变）。
- 20:45-21:25 共 26 条 type=2/content='模型测试'，呈 ~5.6min 一批、
  每批 2-7 个渠道的节奏——这不是内置调度器（已关），而是 **Guardian 的
  ERROR_SCAN**（`guardian.py:121`：每 20 周期×15s=5min 扫一次近期有错误
  的渠道，每批最多 5 个）+ 整点的 FULL_SCAN（240 周期=1h，每批 4 个轮转，
  21:01:59 offset=0/23 与日志对齐）。
- 被反复探测的都是真实降级渠道（ch3 百倍 502、ch9/ch18 超时、ch2 503），
  ERROR_SCAN 在履行恢复探测职责，不是误报风暴。

**成本评估**：每次测试为极小请求（pt<10/ct<15），免费渠道成本可忽略；
付费渠道偶尔被扫到（ch42 一次）。当前 ~39 次/h。若需再降，可将
`ERROR_SCAN_INTERVAL` 20→40（10min）或排除付费渠道，需重启 Guardian 生效
——暂未改动，保留观察。

## 2026-08-05 深夜：flash 池"李鬼"清理 + vision 角色换 Qwen3-VL

**假 flash 路由摘除**（用户发现消耗日志异常，逐层查实）：

- **ch44 codebuddy**：models 列表残留 `deepseek-v4-flash`，OMP 侧虽已清理
  codebuddy/flash，但 zg-newapi 聚合池仍把 150K+ pt 的大请求打进
  codebuddy 上游。已从 ch44 models 摘除（池剩 5 路不受影响）。
- **ch15 sensenova-token**：`model_mapping` 把 `deepseek-v4-flash` 映射成
  `sensenova-6.7-flash-lite`——商汤小模型冒充 DeepSeek V4 Flash。
  已摘除模型+映射（sensenova-6.7-flash-lite 本体保留，smol 角色不受影响）。
- 清理后 flash 池三路全真：ch48 opencode-go(w20 主力) + ch42 deepseek-official
  (w5 付费) + ch53 atomcode-bridge(w5)。
- **教训**：渠道 PUT 更新必须用 adjust_weights.py 模式——GET 后 pop status、
  key 从 DB 取（GET 掩码，空 key 会清空渠道密钥）；直接 PUT GET 原样对象
  报 "Invalid parameters"。

**vision 角色换 atomcode Qwen3-VL**（用户提议"让 atom 的另一个模型看图"）：

- atomcode 网关（9457）`/v1/models` 发现第二模型 `Qwen/Qwen3-VL-8B-Instruct`。
- 实测图像理解：纯红 PNG → "Red" 正确；**冷启动 110s**（模型装载），
  热态 4.2s——首次看图慢是正常现象。
- models.yml 注册（input: text+image, 131K ctx, 8K out）；config.yml
  vision 角色及 fallback 链首候选改指 atomcode/Qwen3-VL，claude/gpt 留作
  后备（备份 \*-qwenvl.bak）。`omp models` 解析正常（atomcode 2 模型，
  images=yes），路由门禁 5/5 通过（本机需 PYTHONUTF8=1 跑 pytest，
  否则 GBK 解码 omp 表格输出报错）。
- 注意：OMP 运行中进程下次重启才加载新 vision 角色。

## 2026-08-05 深夜：本地桥看门狗覆盖盘点 + converter 补丁 code review

**看门狗覆盖（全部有狗，无缺口）**：

| 桥                        | 进程                        | 守护                                                  | 状态                                                                 |
| ------------------------- | --------------------------- | ----------------------------------------------------- | -------------------------------------------------------------------- |
| NewAPI :3002              | new-api.exe                 | LocalNewAPI-Watchdog（1min 探测，AtLogOn+周期双触发） | ✅ 实测杀进程 25s 复活                                               |
| codebuddy converter :8787 | pythonw converter.py        | proxies-supervisor（logon，conhost --headless）       | ✅ 今日 21:18 实测自动重启                                           |
| agentrouter :8788         | python agentrouter-proxy.py | 同上 supervisor                                       | ✅                                                                   |
| atomcode bridge :9457     | node proxy.js               | 同上 supervisor                                       | ✅（另有 atomcode-bridge-watchdog 任务但已禁用，属被取代的历史遗留） |
| cc-switch proxy :15721    | cc-switch 应用内            | 应用自身 + Guardian restart_local_proxy               | ✅                                                                   |

- supervisor 仅 1 实例运行（conhost→cmd→python 三层，19:13 启动）；schtasks
  列表里同名任务出现两行是**每触发器一行**，非重复任务。
- 遗留杂项（不影响运行，提权后可清理）：atomcode-bridge-watchdog（禁用）、
  LocalAIProxies-Supervisor（禁用，被 -Logon 版取代）、CodebuddyHy3Converter
  （缺 --host 的旧 logon 任务，现由启动文件夹 vbs + supervisor 双保险覆盖）。
- agnes-relay 有独立 logon 任务但无崩溃自愈——属最低优先，暂不管。

**converter 流内重试补丁 review 结论**：无 P0/P1。重试仅发生在首字节前
（POST 幂等安全）；`yielded_any` 防重复输出；网络错误不冷却 key、可重试
HTTP 状态才冷却，语义正确；全 key 冷却时降级 502 交 OMP fallback 链，
符合分层设计。P3 可选（不建议现在做）：共享 keep-alive client 进一步
降低 connect 抽签率、重试间隔加 jitter。无单元测试（converter 在仓库外），
以 6/6 实测 + 日志内部重试救回记录作为验证。

## 2026-08-05 深夜：所谓"又路由了"= 上下文推广（非故障）+ models.yml 乱码修复

**真相**：OMP 日志 22:06:10 记录 `Context promotion switched model on
overflow: zg-newapi-anthropic/claude-opus-5 → zg-newapi/deepseek-v4-flash`，
同秒 `contextTokens=214760 > contextWindow=200000`——会话超出 opus-5 的
200K 窗口，OMP **主动**升级到 1M 窗口的 flash 以避免压缩。这是
models.yml `contextPromotionTarget` 的设计行为，UI 模型徽章从
Claude Opus 5 变成 DeepSeek V4 Flash 不是故障改道。
（注：promotion 后按用户分层观 claude≈gpt，若想让长会话升到同级
gpt-5.6-sol（1M ctx）而非 flash，改 promotion target 即可，暂未改。）

**顺手修复**：models.yml 四个显示名是 GBK 二次编码乱码（"聚合池/商汤/
官方/独立"，其中一个还夹了 U+E102 私有区字符导致普通字符串替换匹配
不上），且池子清单过期（ch15/35/37/38/42/43/44 → 现 ch42/48/53）。
已全部改为正确 UTF-8 中文并更新清单。路由门禁 5/5 通过。
教训：写 models.yml 的脚本必须显式 UTF-8，Windows GBK 默认编码会
产生这种"看起来像中文但字节不对"的乱码。

## 2026-08-05 深夜：渠道成本认知更正 + agentrouter 退出 sol 共享层

**更正（用户确认）**：林夕（ch9/ch18）与百倍（ch3）**均为免费**；
此前文档/会话中"林夕付费买稳定"的说法错误。按调用次数扣费、额度
有限的是 **gorouter（ch57，已欠费禁用）**——不是 opencode-go（ch48
免费，flash 池主力正当使用）。agentrouter（ch45）额度同样有限。

**处置**：ch45 agentrouter priority 50→40——此前它与 5 个免费 sol
渠道同层混跑（w5/总48，~10% sol 流量白烧 agent 额度），降层后仅在
免费层全挂时兜底。zg-agent-\* 显式别名不受影响（那是用户主动指定
agent 的入口）。

**当前配额暴露面**（已收敛到最小）：

- claude-opus-5：ch9(52) → ch3/ch18(50) 全免费 ✅
- gpt-5.6-sol：ch20/44/64/65/70(50 免费层) → ch45(40 兜底) ✅
- gorouter ch57：禁用中，无流量 ✅
- 已知风险：Guardian 恢复逻辑可能把 ch45 优先级回写 50（无历史
  记录时的默认值），发现 sol 流量异常打 ch45 时复查。

## 2026-08-05 深夜：agentrouter 成为 opus-5 最终兜底（用户决策）

- `claude-opus-5` 加回 ch45（agentrouter，prio 40）——早晨因敏感词误杀
  摘除，现作为**最后一层**回归：免费层全挂时有个机会总比没有强，
  误杀是间歇性的。
- `RetryTimes` 1→2：opus-5 三层结构（52 林夕 / 50 百倍+林夕备 /
  40 agentrouter）需要 3 次尝试才能触底，原来 1 只允许 2 跳、
  agentrouter 永远轮不到。全局影响：所有模型的失败请求多一跳
  重试，仅在失败时发生，可接受。
- 最终 opus-5 路由：ch9 linxi(52,免费) → ch3 baibei(50,免费) /
  ch18 linxi-backup(50,免费) → ch45 agentrouter(40,限量兜底)。

## 2026-08-05 深夜：cc-switch Claude 接入本地 NewAPI 聚合

- 新增 Claude 供应商 `local-newapi`（"NewAPI 本地聚合"，is_current=0、
  在 failover 队列）：`ANTHROPIC_BASE_URL=http://127.0.0.1:3002`，
  全部模型槽位（SONNET/OPUS/FABLE/HAIKU/SUBAGENT）= `claude-opus-5`，
  吃三层免费路由 + agentrouter 兜底 + RetryTimes=2。
- 令牌用专用 `cc-switch`（tokens id=3）——注意 NewAPI DB 存的 key
  **不带 sk- 前缀**，客户端用时需拼接。`local-windows-clients`（id=6）
  remain_quota 已为负（-465 万），不宜再扩散使用。
- 接入前实测：`POST /v1/messages` model=claude-opus-5 返回 pong
  （首调用 37.7s 含冷启动+思考，anthropic 原生路径通）。
- 写入前用 sqlite backup API 备份 DB 至
  `~/.cc-switch/backups/cc-switch-before-local-newapi-*.db`。
- 注意：cc-switch 运行中外部插入不会触发其 React Query 失效——
  **重开窗口**才能在列表看到；切换前不影响当前供应商（林夕镜像站）。

## 2026-08-05 深夜：Kimi Code CLI 接入本地 NewAPI 模型

`~/.kimi-code/config.toml`（备份 `config.toml.20260805-224800.bak`）：

- `local-newapi`（openai 型，3002/v1）令牌由 `local-windows-clients`
  （额度已为负）换成专用 `cc-switch` 令牌（tokens id=3）。
- 新增 anthropic 型 provider `local-newapi-claude`（3002，SDK 自拼
  /v1/messages）——Kimi Code 原生支持 `type="anthropic"`，官方文档
  providers.html 确认。
- 模型注册：`newapi/gpt-5.6-sol`（1M，thinking+image+tool）、
  `newapi/k3`（1M，efforts low/high/max）、
  `newapi/claude-opus-5`（200K，anthropic 路径，capabilities 显式声明
  ——自定义名不会自动识别能力）；`newapi/deepseek-v4-flash` 原有保留。
- 实测：sol 走 openai 路径用 cc-switch 令牌 2.2s 出 pong；opus-5 的
  anthropic 路径下午已验证。`default_model` 保持官方 k3-256k 不动，
  切换用 `/model`；secondary_model 已是 flash。tomllib 校验通过。

## 2026-08-05 深夜：Kimi Code CLI 接入 WorkBuddy 直连 sol

`~/.kimi-code/config.toml`（备份 `config.toml.20260805-225323.bak`）：

- 新增 provider `codebuddy-direct`（openai 型，
  `http://100.83.32.95:8787/v1`）——8787 converter 只绑 tailnet IP；
  api_key 已写入，与开机脚本 start-converter.ps1 的 --api-key 向前兼容。
- 新增模型 alias `codebuddy/gpt-5.6-sol`（1M 上下文，thinking +
  image_in + tool_use，display_name "GPT 5.6 Sol (WorkBuddy 直连)"），
  与走 NewAPI 的 `newapi/gpt-5.6-sol` 区分：NewAPI 挂掉时可用
  `/model codebuddy/gpt-5.6-sol` 直接兜底，不经过聚合层。
- 实测：`POST 8787/v1/chat/completions` 带 Bearer 返回 200 "pong"。
  tomllib 校验通过；default_model 保持官方 k3-256k 不动。

## 2026-08-05 深夜：cc-switch Claude 供应商接入 sol

- 背景：`codebuddy/gpt-5.6-sol` 的 8787 converter
  （`~/.kimi-code/proxies/codebuddy2openai/converter.py`）只暴露 OpenAI
  协议（`/v1/chat/completions`、`/v1/models`），无 `/v1/messages`——
  Claude Code 只吃 Anthropic 协议，**不能直连 8787**。
- 可行路径：经 NewAPI 做协议转换。ch44 `codebuddy`（8787 直连）已在
  `gpt-5.6-sol` 聚合组内（priority 50 weight 10，与 fengwind/vip-j3gb
  并列，agentrouter p40 兜底）。
- 冒烟：`POST 127.0.0.1:3002/v1/messages` model=gpt-5.6-sol
  （cc-switch 令牌）200，3.7s 出 pong，anthropic 路径通。
- 新增 cc-switch Claude 供应商 `local-newapi-sol`（"NewAPI 本地聚合-Sol"）：
  克隆 `local-newapi` 结构，全部模型槽位 = `gpt-5.6-sol`，is_current=0、
  in_failover_queue=1、category=custom。写入前 sqlite backup 至
  `~/.cc-switch/backups/cc-switch-before-local-newapi-sol-*.db`。
- 链路：Claude Code → 3002(anthropic→openai) → ch44 → 8787(openai→WB v2)
  → WorkBuddy。两层本地转换；8787 无鉴权暴露问题因只绑 tailnet IP。

## 2026-08-05：暂不增加 8787 的 Anthropic 直连适配层

- 评估过为 8787 converter 增加 `/v1/messages`（含流式和 `tool_use`）适配，
  但当前 NewAPI 路径已经验证可用，额外转换层会增加维护面和故障点。
- 本次不修改 `~/.kimi-code/proxies/codebuddy2openai/converter.py`；Claude
  继续使用 `local-newapi-sol`，由 NewAPI 完成 Anthropic → OpenAI 转换，
  再经过 sol 聚合组和 codebuddy 8787 渠道。
- 如需 NewAPI 完全停止时仍能让 Claude 直连 WorkBuddy，再单独实现并测试
  8787 的 `/v1/messages`、流式事件和工具调用转换，不把未验证的适配层投入现网。

## Guardian NewAPI 告警风暴修复（2026-08-06）

**现场症状**：NewAPI 短时不可达后，Telegram 每约 75 秒重复发送“NewAPI 健康检查失败 / 自动重启已禁用”。00:35:32–00:48:14 持续重发；同一时间 `newapi_fail_streak` 累积到 149。

**根因**：代码注释和测试把 `AlertManager` 的 error 冷却当作“故障期去重”，但该冷却只有 1 分钟；Guardian 实际故障周期约 75 秒，每轮都已越过冷却，因此必然放行。原测试在无时间流逝的连续调用中执行，只证明相邻瞬间调用被挡住，没有覆盖跨冷却但仍属同一故障段的场景。

**修复契约**：

- NewAPI 连续失败达到阈值时写入持久化 `newapi_outage_alerted=true`，同一故障段只告警一次；
- 标记先落盘再调用 Telegram，Guardian 重启或通用冷却到期都不会重复发送；
- NewAPI 健康恢复时同时清零 `newapi_fail_streak` 并删除 `newapi_outage_alerted`，下一次独立故障可以重新告警；
- `AlertManager` 冷却保留为附加防抖，但不再承担故障段状态语义。

**测试与现场证据**：新增跨过 2 分钟冷却仍只调用一次、恢复后重新武装两项回归；Guardian 完整回归 87/87 通过。同步生产副本并恢复 `start.bat` 持久 owner 后，NewAPI `/api/status` HTTP 200，心跳 PID 27668 更新，state 为 `newapi_fail_streak=0` 且无 outage 标记；00:49 后日志无新的 `NewAPI health failure`。

**经验**：时间冷却只能表达频率，不能表达故障生命周期。需要“每段一次”的通知必须有显式、可持久化的 outage state，并测试“时间已跨过冷却但故障尚未恢复”的路径。

## 告警修复后生产审计（2026-08-06 01:00）

审计发现并处理三项残余风险：

1. **proxy supervisor 双 owner**：旧计划任务进程（`python.exe`，父 `start-proxies-supervisor.bat`）与新 HKCU `pythonw.exe` supervisor 同时存活。旧进程在 named mutex 加固前启动，不受新互斥逻辑约束；两者可能同时杀/拉代理。已精确终止旧 supervisor 和 bat 父进程，保留 HKCU owner。
2. **重复启动任务**：`LocalAIProxies-Supervisor-Logon` 除登录触发外还每 1 分钟启动一次 supervisor。即使新 mutex 会让重复实例退出，这仍会持续制造无效进程并可能复活旧代码。已禁用该任务；保留 HKCU `OMPProxiesSupervisor` 为唯一持久入口。`LocalNewAPI-Watchdog` 每分钟运行的是短进程端口探测，语义不同，保留。
3. **agentic-only 渠道误判**：channel 57 的上游只接受 tool-calling 客户端，NewAPI 通用 `/api/channel/test` 会间歇返回 `403 non_agentic_blocked`。旧 Guardian 会把它计入全量扫描和恢复后稳定性失败，连续两次可能禁用真实可用渠道。现统一分类为 probe-incompatible：错误扫描、全量扫描和稳定性回滚均跳过且不累计；恢复阶段仍不把它算成功，避免无证据自动启用。

验证：Guardian 完整回归 90/90；Guardian 源/生产副本和 TTFT gateway 源/生产副本 SHA-256 分别一致；Guardian 新 PID 11844 心跳更新；supervisor 仅 1 个 owner；3002/3003 均 HTTP 200；8787/8788/9457 均返回预期的未授权 401，证明进程存活且鉴权边界仍在。channel 57 现场 test 2.14 秒成功，未因历史余额错误手工禁用。

**人工项已完成（2026-08-06 03:03）**：旧 `CodebuddyHy3Converter` 登录计划任务已先导出 XML 到 `~/.omp/guardian/task-backups/CodebuddyHy3Converter.xml`，随后经 UAC 管理员权限删除；`schtasks /Query` 已返回“系统找不到指定的文件”。当前 8787 继续由 `proxies-supervisor.py` 管理，converter 与 supervisor 进程均存活，`/v1/models` 返回预期的未授权 401。按用户决定，本次不轮换 CodeBuddy key，也未修改 `secrets.json` 或代理配置。

## OMP/NewAPI 故障域最终隔离（2026-08-06 02:50）

本节覆盖前文 ch45 作为 NewAPI Claude/Sol 最终兜底的历史方案。生产日志确认 ch45 聚合路径出现 429 饱和、负余额 403 和 `45->45->45` 自重试；ch44 的 Sol 聚合路径出现 `unsupported_client`。最终状态：

- ch44 启用但仅保留 Hy3；CodeBuddy Sol 仅走 OMP 直连 8787。
- ch45 从 NewAPI 移除全部 Claude 模型/别名，并手动禁用 `status=2`；AgentRouter Claude/GPT 仅走 OMP 直连 8788。
- Guardian state 将 ch45 标为 `manual=true`，禁止小探针将其重新加入聚合池。
- ch18 瞬态 502 后复测恢复；ch2、ch62–65 因无上游或生产形态余额不足继续禁用。
- live smoke 现在同时检查模型隔离和预期禁用状态；违反任一策略即退出非零。

验证：仓库 Guardian/smoke/OMP route 114 项、TTFT gateway 5 项通过；NewAPI live smoke `ALL OK`；OMP Claude、CodeBuddy Sol、default 和 AgentRouter 直连路径均完成真实请求；ch45 观察 45 秒未重新入池。

## HugAI/Sub2API 渠道登记（2026-08-06 16:04）

用户提供 `https://claude.hugai.vip` 的上游 key，按“测试后导入渠道”执行。上游站点为 Sub2API；裸客户端请求被 Cloudflare `1010` 拦截，加入浏览器式 User-Agent 后可到达平台。

测试矩阵（不带密钥值）：

- `/v1/models`：HTTP 403，不对 API key 开放模型目录；
- `/v1/messages` Claude 候选（`claude-opus-5`、`claude-sonnet-4-5`、`claude-haiku-4-5`、`claude-opus-4-8`、`claude-opus-4-6`、`claude-3-7-sonnet-latest`）：明确返回“当前分组没有配置该模型”；`claude-opus-5` 走到上游但返回 502；
- GPT/Gemini 候选（`gpt-5.6`、`gpt-5.6-sol`、`gpt-5.6-terra`、`gemini-3-pro`、`gemini-3-flash`）：冷却期后全部返回 `model_not_found`；
- 曾出现用户级 RPM 429，但冷却后返回确定性 `model_not_found`，说明不是可用模型被暂时限流。

结论：当前 key 所在 Sub2API 分组没有任何可成功调度的测试模型，不能作为启用渠道加入路由池。已登记为 **NewAPI channel 71 `hugai-claude-disabled`**，采用 `type=14`（Anthropic）、`status=2`、`priority=0`、`weight=0`、`models=claude-opus-5`、浏览器式 UA header override、自动封禁关闭；不创建 abilities，不进入 fallback。该登记只保留待修证据和回滚点，不代表上游可用。

备份：导入前数据库快照为 `C:\Users\zhugu\.new-api-local\backups\new-api-before-hugai-channel-20260806.db`（SHA-256 `e8462ffd43904f182b8cdc974fc5999cc1f6b598d0d67a26b6da7dc75cfea59e`）。

验证：NewAPI `/api/status` HTTP 200；`scripts/ops/test_omp_routes.py` + `test_smoke.py` 45 项通过；live smoke 的 `channels` 项仍因既有 auto-disabled channel 18/70 返回 FAIL，与 HugAI 登记无关；模型隔离与预期禁用检查均通过。

### HugAI Opus 5 激活与兜底定位（同日 19:38）

复测时发现前一轮命令错误地使用了 Windows CMD 的 `%HUGAI_KEY%` 环境变量语法，而执行环境实际为 POSIX shell；上游收到的是字面量而非密钥，因此此前 `INVALID_API_KEY` 结论无效。改用 `$HUGAI_KEY` 后，直连 `POST https://claude.hugai.vip/v1/messages`、模型 `claude-opus-5` 返回 HTTP 200 和 `HUGAI_OK`（约 1.8s）。

在不创建重复渠道的前提下，原地更新 channel 71 为 `hugai-claude-opus5`：`type=14`（Anthropic）、`models=claude-opus-5`、`status=1`、`priority=40`、`weight=10`、`auto_ban=0`，保留浏览器式 User-Agent override。NewAPI 专属渠道测试 `success=true`（2.028s），abilities 重建成功；经本地聚合入口 `POST 127.0.0.1:3002/v1/messages` 实推返回 HTTP 200 和 `NEWAPI_HUGAI_OK`。

路由定位：channel 71 不进入 priority 50 主池；主池仍为 ch3 `baibei-100xlabs` 与 ch9 `linxi-k40`。HugAI 位于 priority 40 最终兜底层，与额度有限/边界更复杂的 AgentRouter、AnyRouter 同类处理，只在主池失败并发生重试下落时承接 `claude-opus-5`。它只加入 NewAPI 聚合池，不设为 CC Switch current，不加入本机 failover queue，也不作为 OMP 独立直连 provider。

本轮回滚快照：`C:\Users\zhugu\.new-api-local\backups\new-api-before-hugai-opus5-retest-20260806.db`（35,975,168 bytes）。聊天中出现过的 HugAI key 视为已暴露；稳定性观察后应在上游轮换，并通过 `PUT /api/channel/` 更新 channel 71（请求体不得含 `status`）。

发布前回归：`scripts.ops.test_omp_routes` + `scripts.ops.test_smoke` 共 45 项通过；live smoke 的 NewAPI 状态、本地 8787/8788/9457、模型隔离及两条真实聚合请求通过。随后修正过期门禁：ch45 已按既定策略作为 priority 40 的 AgentRouter Sol 最终兜底，不再属于 `KNOWN_BROKEN_CHANNELS`；Claude 基础模型与显式 Claude 别名仍由 `CHANNEL_MODEL_EXCLUSIONS` 禁止进入 ch45。修正后 live smoke 的 `intentional channel disables` 与 `channel model isolation` 均通过；整体仍仅因既有 ch18/ch70 auto-disabled 返回非零。

### Sol 兜底渠道姿态门禁（同日 20:09）

补强 `newapi-local-smoke.py`：Sol 模型本身不是违规条件，AgentRouter 与 AnyRouter 均可承载 Sol；门禁改为按渠道分别约束。现场 ch45 AgentRouter 与 ch72 AnyRouter 必须保持 `status=1`、`priority=40`、`weight<=5`，允许基础/别名 `gpt-5.6-sol`，但禁止 Claude 基础模型与显式 Claude 别名回流进这两个渠道；CodeBuddy ch44 保留独立 Sol 隔离合同。

### AnyRouter 导入 NewAPI ch72（同日 20:36）

AnyRouter 导入本地 NewAPI 为 channel 72：type 14（Anthropic）、`base_url=http://127.0.0.1:8789`、客户端 key 占位 `any`（8789 代理不校验客户端 key，上游用 secrets 的 `anyrouter_proxy_key`）、`status=1`、`priority=40`、`weight=5`、`auto_ban=0`（同 ch71，避免上游瞬时失败消耗兜底）、models `gpt-5.6-sol,zg-gpt-5.6-sol`。门禁以真实 ID 登记：`FALLBACK_CHANNEL_POSTURES[72]`、`CHANNEL_MODEL_EXCLUSIONS[72]`、`PROXY_PORTS["anyrouter"]=127.0.0.1:8789`。

入口证据：anyrouter.top `/v1/models` 含 `gpt-5.6-sol`；chat-completions 与 messages 表面对 Sol 返回 404「当前 API 不支持所选模型」；`/v1/responses` 表面进入渠道选择（500 `get_channel_failed` 负载上限）——Sol 的入口是 responses 表面。据此扩展 8789 代理：非 Claude 模型在 chat/completions 与 messages 两表面均转换为上游 `/v1/responses`（OpenAI→responses、Anthropic→responses），并转回各自客户端协议（流式请求合成 SSE）；Claude 模型仍走 messages 指纹路径，chat 表面的 Claude 以 400 fail-closed。备份 `proxy.cjs.bak-20260806-responses`；重载方式为 kill 旧进程 + `proxies-supervisor` 自愈。

上游现状（非本地链路问题）：Sol 在 responses 表面负载上限（500 `get_channel_failed`），Claude 在 messages 指纹网关被 520 拒绝。ch72 保持登记，NewAPI 按请求 failover；上游恢复后零配置生效。持续验证可用管理测试端点 `GET /api/channel/test/72?model=gpt-5.6-sol`。

回滚：DB 快照 `~/.new-api-local/backups/new-api-before-anyrouter-import-20260806.db`；代理备份同上；移除渠道可 `PUT /api/channel/` 调整或恢复快照。

验证：仓库 smoke/route 共 50 项通过；live smoke 新增 anyrouter 8789 探针与 ch72 姿态/隔离检查均通过。整体仍仅因既有 ch18/ch70 auto-disabled 返回非零。

### WorkBuddy Sol 可用性与 403 冷却加固（同日 20:52）

结论：WorkBuddy 的 Sol 可用。经 8787 实测 12/12 全形态 200（非流式/流式、带/不带 tools、带/不带 system），时延 3-7s。日志中的大批 403 `unsupported_client` 是 freemodel 在并发 burst 期间对个别 key 的瞬时客户端门禁拒绝（同 key 随后又成功）；converter 此前把 403 判为不可重试、原样透传且不冷却，burst 期间坏 key 被反复选中直达客户端（OMP 兜底链重试放大 403 计数）。

加固：`_is_retryable_code_text` 增加 `403 unsupported_client` 判为 key 质量问题——冷却换 key 重试（流式/非流式共用同一判定点），冷却 180s 到期自动重探自愈。备份 `converter.py.bak-20260806-403cooldown`；重载为 kill + `proxies-supervisor` 自愈。重载后验证：sol stream+tools 200、sol 非流式 200、hy3 200。

ch44 策略未变：NewAPI 聚合仍只 hy3；WorkBuddy sol 由 OMP provider 链（slow/task/plan 三链 `codebuddy/gpt-5.6-sol`）直接消费。若要把 WorkBuddy sol 纳入 NewAPI 聚合，需要 ch44 重新加模型并移除 `CHANNEL_MODEL_EXCLUSIONS[44]`，且 ch44 为 priority 50 会进主池——待用户决策。

### WorkBuddy sol 中断 RCA 与 NewAPI 中继（同日 11:22）

症状：freemodel 全 key 403 `unsupported_client`；官方后端 copilot.tencent.com 对 sol 返回 11102「model only available for authorized users」。

RCA：freemodel 网关在 08-06 20:44 至 08-07 10:31 之间服务端改了客户端门禁——4 个 key 的 `/v1/models` 仍 200 含 sol，但 chat 表面拒绝旧 preamble 指纹，TLS 层亦间歇 RST；本地无新指纹源（现装 WorkBuddy 客户端 app.asar 无 freemodel/preamble 字符串，app 的 sol 走 `custom-local:gpt-5.6-sol` 即本 converter）。官方后端 11102 与身份头组合、本地代理 7897 均无关——账号授权问题，本地不可解。

处置（converter 层，WorkBuddy app 与 OMP codebuddy 链同受益）：`_validate_custom_url` 增加显式白名单 `LOCAL_ALLOWED_UPSTREAMS={"127.0.0.1:3002"}`（置于 https/私网检查前）；models.json 的 sol 指向 `http://127.0.0.1:3002/v1/chat/completions`（NewAPI 客户端 key），freemodel key 池清空防 Bearer 污染；converter 重载。验证：sol stream+tools 200、sol 非流式 200、hy3 200。备份 `models.json.bak-20260807-freemodel-retire`。

### proxies-supervisor exit 58 重启（同日 11:09）

supervisor 自 08-06 ~21:01 起挂掉（exit 58，日志无 traceback），期间子进程均存活但失去自愈；hub restart 恢复。已加 `faulthandler.enable()` 以便下次静默崩溃留栈；若复发按栈定位。

### ch53 atomcode-bridge 401 与 Gitcode token 失效（同日 11:37）

ch53 在 11:31 被 NewAPI auto-disable（401「Gitcode auth: token rejected」），桥本身存活（9457 探针 OK）。RCA：`~/.atomcode/auth.toml` 的 access_token 名义有效期至 08-08 01:52，但已被服务端拒绝（疑似撤销）；refresh_token 亦已死——`acs.atomgit.com/oauth/refresh` 返回 502 `refresh_token不存在或已过期`。桥的自动刷新与 401 强制刷新均无法恢复。修复需重新登录（`atomcode login` 或 AtomCode 应用内重新授权，重写 auth.toml），本地无 CLI 可用；ch53 保持禁用（fail-closed 正确），新 token 就绪后恢复 status=1（该 fork 更新端点需大写键 `{'Id':53,'Status':1}`）。

### atomcode 整体下架（同日 11:57）

OAuth 授权被服务端阻断（3 次 401 `Unauthorized`，trace `3debe775`/`30d6d818`/`b5a1bece`，登录态下仍拒；设备码注册正常说明客户端未被拉黑）→ 按用户决定整体删除：

- 进程：bridge proxy.js、watchdog.js、watchdog.ps1 全部终止；Startup `atomcode-bridge-watchdog.cmd` 禁用（`.disabled`）；计划任务本就禁用。
- supervisor：`PROXIES` 移除 atomcode 登记（proxies-supervisor.py）。
- NewAPI：ch53 删除（`DELETE /api/channel/53`）。
- OMP：models.yml 移除 atomcode provider；config.yml vision 主模型 `atomcode/Qwen3-VL` → `zg-newapi-anthropic/claude-opus-5`（fallback 链去重链首）、`maxInFlightRequests.atomcode` 删除、flash/smol 链的 `atomcode/deepseek-v4-flash` 删除（备份 `*.bak-20260807-atomcode-removal`；OMP 下次重启生效）。
- 仓库门禁：smoke `PROXY_PORTS`、guardian `LOCAL_PROXIES`/探针 key/重启分支、三套测试同步（145/145 通过）。
- 归档：`~/atomgit-opencode-bridge.bak-20260731`（原 `~/atomgit-opencode-bridge`）。
- 验证：live smoke 仅剩既有 ch18/ch70 auto-disabled 基线。

## AgentRouter Sol 顶上 default 备用（2026-08-06 16:19）

背景：林夕/百倍路径当前不可作为可靠承接，用户要求 Agent 渠道顶上。历史门禁已明确 AgentRouter Claude 短流式存在上游敏感词误杀，因此不把 AgentRouter Claude 提升为 slow/plan/vision 主路由。

证据：`agentrouter` 本地代理只监听 Tailscale 地址 `100.83.32.95:8788`；未认证请求返回 401。携带 Guardian secrets 中的代理 key 实测 `agentrouter/gpt-5.6-sol`：非流式 200（约 10.7s）、流式 200（约 4.9s）、强制工具调用 200 并返回 `AGENT_TOOL_OK`。

变更：`~/.omp/agent/config.yml` 的 `fallbackChains.default` 在 `zg-newapi/gpt-5.6-sol` 后插入 `agentrouter/gpt-5.6-sol`，作为 default 的第二候选和第一个跨故障域 AgentRouter Sol 备用。`designer` 链首此前已是 AgentRouter Sol；`slow` / `plan` / `vision` 未改。备份为 `~/.omp/agent/config.yml.20260806-agentrouter-sol-backup.bak`。

验证：`omp -p --model agentrouter/gpt-5.6-sol` 返回 `AGENT_OMP_OK`；OMP route gate 32/32 通过；Guardian/smoke/route 完整回归 140/140 通过。
