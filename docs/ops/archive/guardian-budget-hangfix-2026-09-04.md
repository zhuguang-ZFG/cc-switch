# Guardian 周期预算 / NewAPI 上游超时加固与睡眠假象复盘(2026-09-04)

## 结论先行

当日日志排障定性过三个"故障",取证后两个是**整机睡眠的墙钟假象**,只有
一个是真实缺陷;最终落地三处变更(ch124 降级、NewAPI relay_timeout、
Guardian 错误扫描预算感知),**不动**周期线程结构与恢复循环。

## 睡眠假象取证(全部不是软件缺陷)

 overnight 证据:NewAPI GIN 行按小时计数——09-03 21h=1357 → 22h=0 →
23h=8(短暂唤醒)→ 09-04 00-14h 全零 → 15h=1018(唤醒)。Uptime
2026-08-29 20:36 起无重启(睡眠不等于重启)。所谓:

- "Cycle took 56113.2s"——单个周期 15.6h,起点 23:52:37 恰在短暂唤醒,
  醒来 15:27:30 sotamodel TLS 超时后周期完成;墙钟含睡眠时段。
- "23.5h 持有的测试请求"(id 2026090315523683…,09-03 15:52:36 创建,
  09-04 15:27:30 TLS handshake timeout)——其中清醒时段(15:52→22:00,
  约 6h)确为隧道停摆 + NewAPI 侧无上游总超时,属真实缺陷(见下);
  其余时长是睡眠抹平。
- "watchdog 失灵"——watchdog 进程(pid 9076,08-29 起常驻)健康;
  醒来→恢复仅 18s(首个 GIN 15:27:38,恢复周期 15:27:48),远小于
  180s stale 阈值,本来就不应触发。09-02 21:40 它还成功 kill+restart
  过一次旧实例。
- 3,934 条 GIN >300s 行全部为跨睡眠墙钟假象;**醒着时段** /v1 正常
  完成时长在两位数秒级。

本机 DNS 为 fake-IP 模式(解析 sotamodel.net → 198.18.0.241),隧道
客户端停摆时,上游连接建立成功但永不转发;NewAPI 侧无 relay_timeout
时该 goroutine 无限等待。guardian 侧所有 HTTP 调用均有 urlopen 超时
(test_channel 30s / _request 15s / telegram 3s),无需周期线程隔离。

## 变更一:ch124 groq 降级 p49→40

背景:ch124 `groq-qwen3.8-27b` 上游拒绝 NewAPI 适配器发出的
`chat_template_kwargs`(OMP thinking 类请求必 400);当日 2 次
`/v1/chat/completions` 400 **最终**透传客户端(无成功重试),但同日
8 次成功(非 thinking 调用)——不可禁用。

决策:priority 49→40(注意方向:**NewAPI 按优先级降序选路**,数值大
者优先;49→60 会把 groq 变成 qwen3-8-27b 唯一首跳,把 2 次 400 放大
成全部)。40 低于 ch88/ch112(均 49),groq 仅在两个主源同时不可用时
兜底,保留其非 thinking 容量价值。

快照 `new-api-before-ch124-priority-20260904-202307.db`
(155,234,304 B,integrity ok)。回读:p=40/w=1/status=1;abilities
重生成后 qwen3-8-27b 行:112=49、88=49、124=40、113=0(预禁)。

## 变更二:NewAPI relay_timeout 0→900 秒

缺陷:options 表无 `relay_timeout` 键 = 上游调用无总超时。09-03
15:52:36 对 ch93(www.sotamodel.net)的渠道测试因隧道停摆清醒挂
约 6h,直到睡眠-唤醒周期才以 TLS handshake timeout 收场。

决策:`PUT /api/option/ relay_timeout=900`。15min 上限远大于醒着时段
观察到的最长正常完成(两位数秒),不会截断真实长流;同时把"上游悬挂"
从无限收敛到有限。回滚工件:`options-before-relay-timeout-20260904-202320.json`
(整表快照;回滚 = PUT relay_timeout=0 或删除键)。回读已确认 900。

## 变更三:Guardian 错误扫描预算感知

缺陷:`scan_error_channels` 批次最坏 5×30s=150s > 90s 周期预算
(CYCLE_BUDGET_SEC),慢上游日预算被吞,当日预算超限 10-30 次/天
(08-31 达 30 次),OMP 角色检查/指标导出/余额/opus/ability fix/state
cleanup 被整轮跳过。`full_health_scan` 早有 deadline 感知,错误扫描漏配。

补丁(对齐 full_health_scan 既有模式,~/.omp/guardian/guardian.py):
- `scan_error_channels(deadline: Optional[float] = None)`;调用点传入
  `self._cycle_deadline`;
- 每渠道探测前检查剩余预算,≤1s 记 WARNING "Error scan budget
  exhausted after X/Y channels; remaining channels deferred" 并 break;
- test_channel 超时收紧为 `max(1, min(30, remaining))`;
- 轮转偏移改为按**实际扫描数**推进(截断时未扫渠道下轮优先;全量扫描
  时与原为前置推进等价)。

**未做**(评估后否决):周期线程隔离——所有调用有界 + fake-IP DNS 秒
回 + 睡眠自愈,复杂度/线程泄漏风险不值;恢复循环退避——已有
RECOVERY_BACKOFF_BASE=2→MAX=60 分钟,ch72 569 次失败 ≈ 24 天×24 次/天,
符合设计,载荷可忽略;禁用渠道错误/全量扫描——本就有 status==1 过滤,
SYS 测试行来自恢复探测(带退避)。

## 存量 harness 破损修复(与本次补丁无关,一并修复)

`test_guardian.py` 基线本就红:13 errors + 1 failure。原因:
- `make_engine` 用 `__new__` 绕过 `__init__`,缺
  `_probe_soft_failures`(guardian.py:1058 初始化)→ 补属性;
- `test_heartbeat_uses_atomic_replace` 仍断言旧 tmp 命名
  `heartbeat.json.tmp`,而实现已改唯一命名
  `heartbeat.json.<pid>.<attempt>.tmp`(WinError5 修复)→ 改断言。

另新增 2 条预算回归:整批推迟(不探测/偏移不动/告警)、部分扫描(已扫
偏移推进+单条告警)。最终 **142/142 OK**。

## 部署与验证

- 备份:`guardian.py.bak-20260904-hangfix`、`state.json.bak-20260904-hangfix`;
- 重启:`schtasks /end` + `/run "NewAPI Guardian"`,单实例确认
  (pythonw pid 18084,20:29:41 起),周期恢复(opus 行继续),
  重启窗口 53s ≪ watchdog 180s 阈值,watchdog 未干预(日志安静正确);
- watchdog 双"进程"实为 conhost 宿主 + powershell 父子对,非重复
  (Local\CCSwitchGuardianWatchdog mutex 正常)。

## 遗留(用户知悉即可)

- 夜间整机睡眠使 Guardian/NewAPI/中继全停 00-15h:若需 24h 中继/监控,
  需调整电源计划(用户决策,非缺陷);
- 09-04 唤醒后 15:27 那轮的预算跳过为醒后首轮恢复行为,单次,无需处理。
