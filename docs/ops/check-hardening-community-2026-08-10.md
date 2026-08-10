# OMP × NewAPI 检查强化与社区修复参考（2026-08-10）

## 背景

08-10 凌晨事故（new-api 静默退出 + start.ps1 BOM/-Wait 注入 + 看门狗 0x800710E0
静默失效 + 多起渠道状态变更无法归因）暴露的不是单点故障，而是**检测面缺口**：
当时的 `system-health-check.py` 20 项与 `newapi-local-smoke.py` 对这些事故类全部失明。
本次按"每个事故类必须有一个常驻检查"原则补齐，并把上游社区/GitHub 已知问题
映射为本地契约（能修的修，不能修的钉死检测）。

## 一、社区/GitHub 参考与处置

### NewAPI（QuantumNous/new-api）

| 上游 issue | 内容 | 本地处置 |
|---|---|---|
| [#3537](https://github.com/QuantumNous/new-api/issues/3537)（open） | `multi_to_single` 渠道被自动禁用的单 key **永不自动恢复**：`ShouldEnableChannel` 只看渠道级 status=3；`UpdateChannelStatus` 同状态短路；`GetNextEnabledKey` 不重测被禁 key。池只减不增，全程无告警 | 上游未修 → 本地新增 `multi_key_health_violations()`：直读 `new-api.db` `channels.channel_info`（API 不暴露该字段），对 `is_multi_key` 渠道检查 `multi_key_status_list` 中被禁 key。手动恢复流程见 `tabitoken-channel-2026-08-09.md`（DB 直写 + PUT 刷缓存）。当前 ch75(3 key)/ch3(6 key) 均干净 |
| [#1457](https://github.com/QuantumNous/new-api/issues/1457)、#1609 | 自动禁用功能多例失效：依赖状态码/关键词匹配 | 与本机实测一致（403 后 auto_ban 未触发）。**结论不变：不依赖 auto_ban**；把 08-03 防放大策略钉进冒烟契约 `REQUIRED_OPTIONS`：`AutomaticDisableStatusCodes=401,402,403,502`、`AutomaticRetryStatusCodes=408,429,500-503`，选项被漂移即 FAIL |
| [#2788](https://github.com/QuantumNous/new-api/issues/2788)（feature request） | 请求对已手动禁用的渠道跳过定时测试 | 本 fork（rc.23）无此能力 → 定时测试通过即回捞被禁渠道（08-10 ch9/18 两次复活事故的机制）。防复发维持**双锁**（status=2 + weight=0），且双锁现在由冒烟强制校验 |
| [LINUX DO 234649](https://linux.do/t/topic/234649) | `AutomaticEnableChannelEnabled` 语义：任何被禁渠道定时测试通过后自动启用 | 保持 `true`（契约钉死）——它是退化渠道（ch3/ch45）上游恢复后的自动回池路径；代价（毒丸复活）由双锁 + 归因检查兜底 |

### OMP（can1357/oh-my-pi，本机 17.2.12）

| 上游 issue | 内容 | 本地处置 |
|---|---|---|
| [#2879](https://github.com/can1357/oh-my-pi/issues/2879)/#2683/#2687（closed） | 长活跃流被误判 operation timeout | **已在本机版本落地**：超时改为 idle 语义。dist 实测默认 300s（`oMn=gMn=300000`），reasoning 模型另有加长 |
| [#3301](https://github.com/can1357/oh-my-pi/issues/3301)（open） | 请求按模型/提供者配置超时 | 部分满足：配置项 `providers.streamIdleTimeoutSeconds`（-1=用默认，0=关 watchdog），环境变量 `PI_STREAM_IDLE_TIMEOUT_MS` / `PI_STREAM_FIRST_EVENT_TIMEOUT_MS`。**纠正旧结论**：『OMP 超时 60s 包内置不可配置』已过时，17.2.12 为 300s idle 且可配。当前无需改（默认 300s 足够覆盖 thinking 首 token） |
| v17.2.4 release | opaque HTTP 400 重试修复（OpenRouter DeepSeek 特定路径） | 范围窄，不视为对『NewAPI 包装 400 → turn 硬死』放大器的通用修复；该放大器仍靠 models.yml 窗口上限防 |

## 二、检测面新增（事故类 → 检查）

`system-health-check.py`（20 → 24 项，仍 ALL GREEN）：

| 事故类 | 新检查 |
|---|---|
| 看门狗挂起后 IgnoreNew 以 0x800710E0 拒绝触发 | `看门狗计划任务`：LastTaskResult（hex 显示）+ 上次运行 <10min；0x800710E0 附事故提示 |
| new-api 半死（进程在/端口死，或反之） | `new-api.exe 进程存活`（tasklist），与端口检查互补 |
| start.ps1 被剥 BOM / 注入 -Wait | `start.ps1 完整性`：BOM 必须存在；`Start-Process` 行不得含 `-Wait`（注释里的字样不误报） |
| PS 5.1 ANSI 解析错位 | `watchdog.ps1 ASCII 契约`：全文必须 ASCII |

`newapi-local-smoke.py`：

- **禁用归因矩阵**（核心新增）：`status=2 ∧ auto_ban=1` = fork 内部 auto-ban 自调（机器行为，预期）；
  `status=2 ∧ auto_ban=0` 或 `status=3` = 必须可归因——在 `KNOWN_BROKEN_CHANNELS`、
  `DEGRADED_ACCEPTED_DISABLED`（本地自动化按下降级渠道，自动启用回池）或 Guardian
  `state.json` 恢复队列之一，否则 FAIL。此前 status=2 的 auto-ban 完全不在检测面内
  （03:20 ch75 被 auto-ban 时就是靠人肉发现的）。
- **双锁强制**：隔离渠道 status≠1 时 weight 必须=0（此前只查 status；本次把
  2/20/62/63/64/65/70/74 全部补齐 weight=0，备份
  `~/.new-api-local/backups/channels-before-doublelock-weight-20260810.json`）。
- **池冗余**：`MIN_ENABLED_CRITICAL_MODELS`（claude-opus-5 / deepseek-v4-flash ≥1），
  0 启用 = FAIL（"503 No available channel" 前置检测）；当前 opus 池单渠道 ch75
  在明细中可见（非 FAIL，等上游恢复自动回池）。
- **多 key 静默退化**：见 #3537 行。
- **fallback posture**：退化禁用渠道（DEGRADED_ACCEPTED_DISABLED）豁免 status 要求，
  但 pri/weight 漂移仍查——回池时必须落回正确层级。
- **选项钉死**：`REQUIRED_OPTIONS` 增加两个状态码选项（见上表）。

## 三、本次池变更与归因结论

- ch3 baibei：12:04 被一次批量禁用（同批 9/18/62-65，GIN 自调签名，非 Guardian
  state 在案——Guardian 只降权 24→12 于 09:06）；判定为某次运维操作的批量隔离，
  列入 `DEGRADED_ACCEPTED_DISABLED`（baibei 上游 502 数小时，测试通过后自动启用回池）。
- ch45 agentrouter：22:05 被禁，渠道自带 auto_ban=1（上游 429/503 抖动触发内部
  auto-ban），机器行为，列入 `DEGRADED_ACCEPTED_DISABLED`。
- ch20 fengwind-gpt56sol：08-05 23:26 禁用（sol 故障路由全局清除决策），此前一直
  不在任何契约集内（检测盲区）→ 本次收入 KNOWN_BROKEN + Guardian 排除集 + 补双锁。
- ch73：15:06 被重新启用且**未同步契约**（并行会话在做 codex-relay 修复，22:34
  仍在测试；Guardian 全扫描记录其上游 502）。冒烟按设计持续 FAIL 提醒：修复完成
  前不得从 KNOWN_BROKEN 移除，修复完成后三处同步移除。
- 排除集三处同步（guardian 运行时 + 仓库镜像 + smoke）：{2,9,18,20,57,62,63,64,65,
  70,71,73,74}；Guardian 已重启生效（pid 5816，备份 guardian.py.bak-20260810-exclusions）。

## 四、验证

- `system-health-check.py`：24/24 ALL GREEN（含全部新检查）。
- `newapi-local-smoke.py`：收敛到 2 个**预期内真实告警**——
  1. `intentional channel disables: 73 re-entered service`（codex-relay 修复中，上游仍 502）；
  2. `fallback channel posture: 72:anyrouter=status=2`（08-10 02:5x 已记录，anyrouter 上游限流）。
  其余全绿：归因 unexpected=none、双锁、池容量、多 key、选项、两条真实冒烟（634ms / 6.1s）。
- Guardian 重启走 watchdog 设计路径（taskkill → schtasks /Run），心跳 12s 内恢复。

## 五、遗留观察项

1. **RetryTimes 口径不一致**：live=3，08-10 01:5x 文档称『RetryTimes=1（08-03 既定）』。
   未钉进契约，待用户确认后统一（影响 408/429/500-503 的重试次数）。
2. opus 池单渠道（ch75）持续到 ch3/ch45 上游恢复；若 ch3 长期 502，评估 ch75 升主路由
   （02:0x 文档既定观察项）。
3. ch71 已从 NewAPI 删除，两处集合保留占位防 ID 复用。
4. 01:17 new-api 静默退出死因仍未查明；本次新增的进程/任务检查只能缩短发现时间，
   不能定因。若再发：先留 stderr.log + Windows 事件查看器（Application 崩溃事件）。
