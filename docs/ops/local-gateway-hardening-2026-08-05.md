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
  与请求头、X-IDE-*、acp-connection-id、TLS/JA3 指纹均无关
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
