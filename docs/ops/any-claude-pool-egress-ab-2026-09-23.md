# any-Claude 池出口 IP A/B 实验（2026-09-23 晚）

## 结论（先读）

**any 渠道 `claude-opus-5-5[1M]` 的 429/503 与出口 IP 无关，是上游 Claude 池全局关闭。**
36 个 Clash 出口 IP（HK/SG/TW/US/CA/JP/ID/TH/UK/FR/DE/TR + Hy2 子节点）+ 家宽直连，429 一视同仁；
经 8789 指纹桥（复刻 claude-cli 指纹）与真实 claude-cli 直连行为一致。本地无可修项，
只能等开窗；检测节奏已从 30min 收紧到 5min。

## 背景

用户持续反馈「any 渠道 opus 用不了，别人都在用」。白天已排除：key 封禁（指纹一致、
额度不限）、`[1M]` 后缀过滤（429 而非 404）、本地传输链路（141KB/59KB 真实流量到上游）。
当晚剩余假设：**出口 IP 被风控**（截图他人配置走 `https://127.0.0.1:7890` 直连类代理）。

## 实验方法

1. **桥改造（默认无行为变化）**：`proxy.cjs` 增加 config 驱动的 upstream 转向：
   `config.json` 可选 `"upstream": {"host","port"}`，把上游 TCP 连到本地端点，
   TLS servername/证书校验仍指 `UPSTREAM_HOST`（对指纹门透明）。缺省/非法 = 直连。
2. **CONNECT 转发器**：`forwarder.cjs` 监听 127.0.0.1:8791，把到 `anyrouter.top:443`
   的连接经 HTTP CONNECT 打进本地 Clash mixed 端口 7897。端到端 TLS，转发器只见密文。
3. **轮换探针**：`egress-ab-probe.cjs` 经 Clash API（127.0.0.1:9097）逐个 PUT
   `/proxies/悍刀行` 选节点，等 1s，经 8789 桥发最小 haiku 探针 ×2（max_tokens=16），
   200=OPEN / 429,503=closed / 其它=异常；结束时还原原选中节点。探针是 429 拥堵拒答，免费。

## 结果（2026-09-23 22:00-22:03 本地）

- 对照（原节点 🇸🇬 新加坡4 电信2x，出口 103.190.179.2）：429 closed。
- 轮换 36/36 节点：**全部 closed**，无一个 open，无一个异常码。
- 版本标记（22:40 复测）：billing_header/UA 从抓包版 `2.1.220.76a` 升到实机
  `2.1.267`（UA 与 billing 同步改，supervisor 重启生效），haiku/opus 探针仍 429。
  拥堵期无开窗对照，**无法正向证伪版本门控**；但此后桥发出的请求与全体真实
  用户（2.1.267）同版本，后续窗口待遇一致。真实 CLI billing 头 build 后缀未
  全量复刻，深查需重新抓包（维护路径）。canary 探测自动使用已升级的桥。
- 选择已还原；桥已还原直连（config.json 去掉 upstream 键，重启后日志无 pivot 行，探针 429 正常）。

## 处置

1. **canary 节奏 30min → 5min**（`\AnyRouter Window Canary`，MultipleInstances=IgnoreNew
   防重叠；重复 Interval=PT5M，Duration 3650 天）。
   回滚：`New-ScheduledTaskTrigger -Once -At '2026-08-15T20:23:00' -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)` + `Set-ScheduledTask`。
   注意：canary 的 Telegram 告警依赖 `secrets.json: telegram_proxy` = 本地 Clash 端口，
   **Clash Verge 必须保持运行**，否则告警链路断。
2. 桥 pivot 代码保留（默认关闭，供未来单点试验）；`forwarder.cjs`/`egress-ab-probe.cjs`
   留作工具，需要时手动拉起。
3. 桥指纹版本标记已升到实机 `2.1.267`（`CC_VERSION` + config billing_header），比
   抓包版更贴近当前 CLI；CLI 自动升级后需同步更新（或重新抓包）。
4. 备份：`proxy.cjs.bak-20260923-215806-egress-ab`、`config.json.bak-20260923-215806-egress-ab`。

## 「别人都在用」的解释

同一时刻全部出口+直连全灭，说明在用的人是**在短暂开窗口缝挤进去的**（我们自己的
`CLAUDE_CODE_MAX_RETRIES=15` 今天 17:00 也成功过 3 次），或使用了我们无法核实的中转/池。
不是指纹/IP 被针对。

## 使用建议（不变）

- 直接重发提示词：每次发送 = 15 次重试抽签，429 不耗额度。
- 等 canary Telegram `closed→open` 告警后立刻重发（现在 5 分钟一探，告警延迟 ≤5min）。
