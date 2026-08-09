# NewAPI 宕机与看门狗失效事件（2026-08-10 凌晨）

## 事件

01:17 前后 new-api.exe 静默退出（stderr 无 panic/fatal，尾部只有上游渠道常规 500/502），
01:18 健康检查告警。同分钟 `~/.new-api-local/start.ps1` 被不明工具改写（mtime 01:17），
改写引入了两个问题，叠加导致"自动重启看起来被禁用"。

## 根因（两层）

1. **BOM 被剥**：改写后的 start.ps1 是 UTF-8 无 BOM。PS 5.1 按 ANSI/GBK 解析无 BOM 脚本，
   中文注释字节错位，第 40 行日志轮转处 `$lf` 绑定为空直接崩溃，脚本根本走不到启动逻辑。
   （与 2026-08-05 Guardian `secrets.json` BOM 坑同源；`watchdog.ps1` 文件头注释早有预警。）
2. **新增 `-Wait` 破坏看门狗语义**：改写把启动动作换成 `Start-Process cmd.exe -Wait`。
   `watchdog.ps1` 是**同步**调用 start.ps1 的（`& powershell ... start.ps1`），`-Wait` 使看门狗
   实例随 new-api 存活期一直挂起；计划任务 `MultipleInstancesPolicy=IgnoreNew` 于是每分钟以
   **0x800710E0**（The operator or administrator has refused the request）拒绝新触发——
   看门狗任务状态"已启用"但永远跑不了新实例。更危险的是 new-api 整个生命周期被绑进
   计划任务进程树（任务停止/断电策略会连带杀 new-api）。

时间线还原：看门狗 01:27:42 触发 → 发现 3002 不可达 → 调 start.ps1 → 01:27:49 拉起
new-api.exe（PID 34488）→ start.ps1 因 `-Wait` 不返回 → 实例挂起，后续触发全被拒绝。

## 修复

- start.ps1 补回 UTF-8 BOM（备份 `start.ps1.bak-20260810-0121-nobom`）。
- 移除 `-Wait`，恢复"启动后立即退出"的分离语义，并在脚本内注释禁止再加 `-Wait`
  （备份 `start.ps1.bak-20260810-blocking-wait`）。
- 清掉挂起的看门狗进程树（`taskkill /F /T /PID 23964`，含其持有的 new-api 34488）。

## 验证

- 01:35:49 看门狗分钟触发自动拉起 new-api.exe（PID 24816），全程无人干预。
- 看门狗实例跑完即退（无残留 watchdog.ps1/start.ps1 进程），任务"上次结果"连续为 0。
- `scripts/ops/system-health-check.py` 20/20 ALL GREEN。

## 踩坑备忘

- **PS 5.1 脚本改 UTF-8 必须带 BOM**：任何工具改写 `.ps1` 后先 `head -c 3 | od -c` 验 BOM，
  或干脆像 watchdog.ps1 一样保持 ASCII-only。
- **Edit/写文件工具会丢 BOM**：编辑带 BOM 的 PS1 后必须重新补 BOM 再验证解析
  （`PSParser::Tokenize`）。
- **被看门狗同步调用的启动脚本绝不能阻塞**（`-Wait` / `cmd /k` 等），否则挂起 +
  IgnoreNew = 看门狗静默失效；故障表现（任务"已启用"、每分钟触发）与真实原因隔一层，
  排查看"上次结果"是否为 0x800710E0。
- new-api.exe 01:17 的初始死因仍未查明（无崩溃日志）；同一分钟 start.ps1 被改写，
  疑似同一操作所为，如有其他会话/工具在操作该目录需留意。

## 同日追加：OMP default 链跨家族降级修复（01:5x）

- 事故放大因素：OMP `retry.fallbackChains.default` 第二跳直接是 `zg-newapi/k3:max`，聚合层一次
  瞬时 502/403 就把主会话从 Claude 降级成 Kimi。slow/plan 链早有同家族缓冲跳，default 独缺。
- 修复：default 链首插入 `zg-newapi-anthropic/claude-opus-4-8`（与 slow/plan 对齐），
  备份 `~/.omp/agent/config.yml.bak-20260810-default-chain`。YAML 校验通过。
- 承载验证：`claude-opus-4-8` 启用渠道实测 ch75 tabitoken 1.4s / ch45 agentrouter 6.7s 均 success。
- 生效条件：OMP 配置有进程内缓存（踩坑 3），需重启 OMP 才加载新链。
- 遗留：ch18 linxi 余额耗尽仍在池（403 毒丸）、ch9 同嫌疑、ch3 优先级 57→51 漂移、ch75 未全面评估
  （均已在下节「聚合池整治」处置）。

## 同日追加：claude-opus-5 聚合池整治（02:0x）

处置（按 08-05 既定方案对齐）：
- ch18 linxi-k40-opus5-backup：余额耗尽（403 insufficient balance）且 auto_ban 未触发 → 手动禁用，
  消除"主路由一抖就踩死渠道"的毒丸。linxi 为远端渠道，不在 Guardian 自动恢复契约内，禁用不会被回捞。
- ch9 linxi-k40：实测有余额但 18.3s 慢 → 降回降级备用位 pri 40 / w2（原 pri 50 / w10）。
- ch3 baibei-100xlabs：恢复主路由优先级 pri 57（原漂移至 51）；当前上游 502 抖动中（Cloudflare origin），auto_ban=0 保持观察。
- ch75 tabitoken：实测 opus-5 7.0s / opus-4-8 1.4s 健康，保留 pri 50 / w7。

验证：渠道终态 ch3(pri57/w20) > ch75(pri50/w7) > ch45(pri40/w5) > ch9(pri40/w2)，ch18/57/72 禁用。
真实聚合冒烟 `POST /v1/messages claude-opus-5` HTTP 200 / 2.3s，由 ch75 承接（ch3 502 期间正确 failover），
客户端不再收到 403。

遗留观察项：ch3 baibei 上游 502 为对方源站问题，若长期不恢复需评估主路由替换（ch75 是候选）。

## 同日追加：client_gone 流错误归因（01:5x）

现象：NewAPI 日志大量 `stream_status: error / end_reason=client_gone`（08-09 全天 150+，00 时峰值 43）。

归因（两类，均已消源）：
1. **01:16-01:17 批次**：随 new-api.exe 01:17 死亡产生（进程消亡取消在途流上下文），
   随进程恢复 + 看门狗修复不再复发。
2. **慢性 ~90s 批次**（00:59/01:10/01:13/01:14 等）：token `local-windows-clients`（OMP 及本地工具共用），
   清一色 `/v1/messages?beta=true` claude-opus-5 流，特征 FRT 48-77s + completion_tokens≤1 + 墙钟 ~90s 被取消。
   链路还原：ch3 baibei 退化（慢/502）→ 池内 failover 落到慢渠道 ch9 linxi（当时 pri 50/w10）或死渠道 ch18
   → 首token拖到 48-77s → 客户端 ~90s 看门狗放弃 → client_gone。**已扣费配额 ~22k/次但 0 产出**。
   客户端取消是保护性正确行为，根因在聚合池退化——已由本次池整治消源
   （ch18 禁用、ch9 降 w2、ch3 回 pri 57、ch75 7s 健康位）。

验证：01:35 重启后至 01:56 client_gone **0 新增**；claude-opus-5 请求 2-18s 完成（ch75 主力承接）。
遗留观察：若池健康后 90s 级取消仍高频复现，再单独排查持 `local-windows-clients` 的客户端超时配置
（cc-switch 代理 first_byte=25s/idle=180s、TTFT 网关 60s 均已排除为非 90s 来源）。

## 同日追加：OMP retry.maxRetries 2→3（01:5x，用户决策）

- 分析结论：stall 被取消的请求配额在请求发出时已固定（prompt+cache write 实测 22k，completion≤1），
  改短超时只能省墙钟、省不了 token；用户明确选择**保可用性**：宁可多烧重试配额也要保住 Claude 主模型不降级。
- 变更：`retry.maxRetries: 2 → 3`（baseDelayMs 3000 / maxDelayMs 60000 不变），
  备份 `config.yml.bak-20260810-maxretries`。需重启 OMP 生效。
- 效果：主模型 stall/错误时 OMP 在同模型上重试 3 次才走 fallback 链（default 链第二跳已是 claude-opus-4-8），
  聚合池健康前提下重试命中健康渠道（ch75 等）概率高，降低降级到 k3 的概率。
- 未动：TTFT 网关 60s 语义超时（thinking 不计语义为既定设计契约，有测试锁定）；
  NewAPI RetryTimes=1 及对 401/402/403/504 不重试（08-03 既定防放大策略）。

## 同日追加：02:07 二次 403 与 linxi 全灭（02:2x）

事件：02:07 又一次 `403 insufficient balance`（150K in / 509 out / 126.3s 后报错），
OMP 按新 default 链降级 `claude-opus-5:max → claude-opus-4-8`——**新链生效**（说明 OMP 已重启加载新配置）。

根因链：
1. **linxi 余额彻底耗尽**：ch9 在 01:48 渠道测试还通过（18.3s，残余余额），02:08 测试已 403；
   ch9/ch18 同账号先后全灭。
2. **ch18 被自动回捞**：NewAPI 选项 `AutomaticEnableChannelEnabled=true`——被禁用渠道一旦测试通过就自动启用
   （内部行为，不写 manage 日志）。01:50 前后 linxi 还有残余余额、测试通过，ch18 被静默回捞进池，
   余额耗尽后成为 403 毒丸。同期 ch9 的 pri/weight 被改写为 50/20（管理日志无记录，写入者未查明，
   与 01:17 start.ps1 被改写并列为本晚第二起未归因变更）。
3. ch3 baibei 持续 502（每分钟级），主路由名存实亡，流量实际靠 failover。

处置：ch9 + ch18 再次手动禁用（status API）。余额为 0 期间测试只会失败，自动启用不会触发，禁用可保持。
验证：池终态 ch3(57/20) > ch75(50/7) > ch45(40/5)，ch9/18/57/72 禁用；真实冒烟 200 / 2.1s。

踩坑备忘：
- **`AutomaticEnableChannelEnabled=true` 会对抗手动禁用**：只要测试还能通过，被禁渠道就会被静默回捞。
  余额耗尽的渠道在余额恢复后会自动复活——linxi 充值后需重新评估 ch9/ch18 是否回到降级备用位。
- NewAPI `AutomaticDisableStatusCodes=401,402,403,502` 且 ch18 `auto_ban=1` 但 403 后未见自动禁用生效，
  auto-disable 对该 fork 的 403 路径不可靠，不能依赖。
- `AutomaticRetryStatusCodes=408,429,500-503` 不含 403——余额类错误必穿透到客户端，池内必须没有死余额渠道。
- 观察项：ch3 若 502 长期不恢复，主路由切 ch75（已实测 2-7s 稳定）。

## 同日追加：02:28 禁用被回捞、02:47 双锁止血与 ch57 毒丸（02:5x）

事件：02:14 手动禁用 ch9/ch18 后，02:28:11/15/41 manage 日志出现三条 `channel.status_update`
（username=admin），ch9/ch18 被重新启用且权重回到 50/20、50/9，02:29–02:45 再次穿透
`403 insufficient balance`，OMP 连续降级到 claude-opus-4-8。

归因排查（02:47–02:56）：
- **Guardian 排除**：其 `check_and_enable_recovered_channels` 只遍历自有 `disabled_channels`
  列表（70/73/72/39），不含 ch9/ch18；guardian.log 该时段无 enable 动作、无 Telegram 告警；
  02:28:08 的 ch72 恢复探测失败（recovery_failures=31），未产生 enable。
- **DX-Ops 冒烟排除**：`scripts/ops/newapi-local-smoke.py` 全程只读（GET），无写渠道能力。
- **NewAPI 内部 auto-enable 存疑但证据不足**：ch9/ch18 余额为 0、测试只失败，理论上不触发。
- **剩余嫌疑**：其他持有 admin 凭据的会话/脚本手动调用了 status API（本晚第三起未归因变更，
  与前两起——start.ps1 改写、ch9 权重改回 50/20——同源可能）。

处置（双锁）：ch9 + ch18 再次禁用（status=2）**并 weight=0**——即使 status 被回捞，
权重 0 也抢不到流量。备份 `~/.new-api-local/backups/channels-before-zeroweight-20260810-0247.json`。

新发现毒丸 ch57 gorouter：smoke 黑名单校验发现其被重新启用（pri40/w6），渠道测试
403「预付费额度失败, 用户剩余额度 $0.166 < 需要 $0.20」——同样已 禁用 + weight 0 双锁。

防复发加固：
- `scripts/ops/newapi-local-smoke.py` 的 `KNOWN_BROKEN_CHANNELS` 加入 ch9
  （ch18/57 本就在列），定时冒烟会在死渠道回捞时 FAIL 报警。
- 冒烟复跑收敛：唯一剩余 FAIL 为 `fallback channel posture: 72:anyrouter=status=2`
  （契约要求 ch72 常开做兜底，当前 anyrouter 上游不可用，属真实告警，保留）。

ch72 anyrouter 实测结论（02:53–02:55）：
- 指纹代理（127.0.0.1:8789）转发正常到达上游，但 anyrouter 对 Claude 模型持续
  `429 Service Unavailable`（冷却 90s 后依旧）；渠道测试报
  「当前模型 gpt-5.6-sol 的使用已经达到上限」——**上游额度/限流问题，非本地配置缺口**，
  保持禁用待上游恢复，不靠用户操作。
- ch72 models 列表 gpt-5.6-sol 在最前，NewAPI 渠道测试只测到被 cap 的 gpt；
  Claude 实际可用性需以上游 429 消退后实测为准。

验证（02:55–02:56）：
- 池终态：ch3 baibei(57/20) > ch75 tabitoken(50/7) > ch45 agentrouter(40/5)；
  ch9/18/57/72 全部禁用（9/18/57 weight=0）。
- 真实冒烟：`POST /v1/messages claude-opus-5` 200 / 1.7s，`claude-opus-4-8` 200 / 1.2s。

踩坑备忘（追加）：
- **OMP 模型名后缀（`:xhigh`/`:max`）是 OMP 侧 effort 语法，OMP 发出前会剥掉**；
  直接拿带后缀的模型名打 NewAPI 会 503「No available channel」——烟测必须用基础模型名。
  今晚一度误判"禁用 ch9/18 导致 opus-5 无渠道"，实为烟测姿势错误的假警报。
- 单禁用不保险：凡被列入隔离的渠道一律 `status=2 + weight=0` 双锁，防任何调用方回捞后立刻吃流量。

## 同日追加：default 角色取消故障路由（03:0x）

用户决策：default 角色不再走 fallback 链（不要 opus-4-8/k3 降级，失败就报错）。
- 改动：`~/.omp/agent/config.yml` 删除 `retry.fallbackChains.default` 整条
  （opus-4-8 / k3:max / kimi-for-coding / opencode-go 四跳全移除），
  `maxRetries: 3` 保留——同模型重试 3 次仍失败则直接报错，不降级。
  其余角色（slow/plan/vision/designer/bigctx 等）链不动。
  备份 `config.yml.bak-20260810-default-noroute`。**需重启 OMP 生效**。
- 注意：这是回滚 01:5x 的"default 链插缓冲跳"方案；当时要防的跨家族降级
  现在由"不降级"替代——聚合池毒丸（ch9/18/57）已清是前提，池再脏时
  default 会直接报错而不是悄悄换成别的模型，用户可按报错显式处理。
