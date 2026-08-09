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
