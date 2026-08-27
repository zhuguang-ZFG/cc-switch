# OMP 配置优化与官方 18.0.3 升级（2026-08-23）

本日三项维护：ch61 测试模型修复、7 个候选模型注册入网、OMP 二进制从陈旧自编译
build 换到官方 v18.0.3。全部变更先探测后落地，均有回滚物。

## ch61 mercury-2 补设 test_model（消除误禁风险）

症状回顾：ch61（inceptionlabs-mercury2，唯一服务 `mercury-2`）历史上被
184/184 零输出探测反复误禁（见 `omp-model-config-review-2026-08-03.md`）。当日
复核发现根因是 `test_model=None`——NewAPI 的通道测试没有明确目标模型。手动管理
探测 `GET /api/channel/test/61?model=mercury-2` 实测通过（200 success, 4.4s）。

变更：经 admin API 更新 `test_model='mercury-2'`，其余字段不变。
回滚快照：`~/.new-api-local/backups/channel-61-before-testmodel-20260823-174920.json`。

### 关键实证修正：「PUT 会写坏 key」的条件范围

`local-gateway-hardening-2026-08-05.md` 记录该 fork 的 GET 会把 `key` 脱敏为空串；
`longcat-chore-roles-agnes-relay-timeout-2026-08-19.md` 因此放弃 PUT 改走直连 SQL。
本次实测补充了关键条件：

1. **PUT 详情端点（`GET /api/channel/61` 的返回体）会 400**（`Invalid parameters`），
   字段形状不被接受——longcat 当年撞的就是这个。
2. **PUT 列表端点条目（`GET /api/channel/?p=0&page_size=200` 里同名项，去掉
   `status` 字段）成功**（沿用 `add_imagic_extra_models.py` 模式）。
3. **提交空 key 是安全的**：后端对空 key 保留原值。证据链——PUT 前直读 DB 观测
   `key` 前缀 `sk_f1c254320`；PUT 后直读 DB `len=35, prefix='sk_f1c254320'`
   完全一致；PUT 后管理探测两次通过；relay `/v1/chat/completions`（mercury-2 经
   ch61）200 OK 正常出内容。

结论修正为：**此 fork 上「GET→改→PUT」必须显式把 `key` 置空提交**（后端保原值）；
危险的只有「把脱敏回显原样写回」和「使用详情端点形状」。验证动作固定为三件套：
DB key 前缀比对 + 管理探测 + relay 实测。

## 7 个模型注册进 OMP zg-newapi（先探测后注册）

对启用通道上有、OMP 未注册的 26 个模型名分诊：多数为已注册模型的变体/渠道别名
（`deepseek-v4-flash-*`、`claude-haiku-4-5[1M]`、`zg-agent-*` 等），收编只加噪音。
真正的新能力逐个 relay 实测（第一轮全 503 为本机 NewAPI CPU 过载保护拦截，与模型
无关，重试后见真章）：

| 模型 | 渠道 | relay 探测 | ModelRatio |
|---|---|---|---|
| sensenova-6.8-flash-lite | yjs-free ch110 | OK | 0 |
| gpt-5.6-luna | opencode-go ch106 | OK | 0 |
| grok-chat-fast | seeseed1ck-hydrogel ch89 | OK | 0.5 |
| kimi-for-coding-highspeed | kimi-official-k3 ch33 | OK | 2 |
| nemotron-3-ultra-550b-a55b | yjs-free ch110 | OK | 0 |
| diffusiongemma-26b-a4b-it | yjs-free ch110 | OK | 0 |
| dots-3-note-preview | yjs-free ch110 | OK | 0 |

`sensenova-u1-fast` 跳过：abilities 有行但上游 404（`model is not found`）——不注册
不可路由模型。定价全部已有配置，无 37.5x 兜底计费风险。元数据按家族先例镜像
（kimi/nemotron 262K；luna 对齐 sol 400K/128K；dots 对齐既有 prev 条目含 image 输入）。

校验：注册前备份 `~/.omp/agent/models.yml.before-sota-candidates.bak`；
`omp models` 解析通过且 7 项全部可见；路由门禁 38/38。

## OMP 二进制升级：陈旧自编译 build → 官方 v18.0.3

发现链：`omp --version` 报 18.0.0，但 bun 全局包已是 `@oh-my-pi/pi-coding-agent@18.0.3`
（npm latest）；`dist/cli.js@18.0.3` 内仅含 18.0.3 版本串 → `~/.bun/bin/omp.exe`
（151MB，MZ 头，Bun 编译产物）是更早源码的手工编译物。bin 目录的 .bak 序列
（15KB 启动器 bak + 151MB bak）证明它替换过 bun 原生启动器。

升级路径取舍：

- `omp update`：重装 npm 包到 18.0.3 成功，但**不会覆盖自编译 exe**（updater 自己
  提示仍报 18.0.0 并建议官方安装器）。
- 官方安装脚本（omp.sh/install）：默认装 `~/.local/bin`（与现有 `~/.bun/bin` 并存
  造成 PATH 歧义），binary 模式面向 POSIX——不适用于本机布局。
- **采用**：GitHub releases v18.0.3 的 `omp-windows-x64.exe`，SHA-256 与官方
  `SHA256SUMS.txt` 一致（`63c5bb8c…c57b5`），换入 `~/.bun/bin/omp.exe`。

编译路线否决说明：包内 `scripts/build-binary.ts` 依赖 monorepo 工作区兄弟目录
（`../stats`、`../collab-web`、`../natives`），node_modules 安装态缺失，不可用。

升级后验证：

- `omp --version` → 18.0.3。
- SOTA 扩展 r7 未受影响：repo/live SHA parity 保持 `2b88c78e…`。
- 门禁全绿：`omp models` exit 0；扩展单测 23/23；路由门禁 38/38。
- **原始故障场景复测**：open-stdin pipe 条件下 18.0.3 与旧版行为一致（挂起至
  ceiling，stdout 空）——CLI 层从未修复该行为，也无需修复：r7 扩展的生产路径是
  `stdio:['ignore','pipe','pipe']`，在 18.0.3 上实测收敛 `exit=0, 10.7s,
  stdout="OK"`。

回滚物与生效时机：`~/.bun/bin/omp.exe.pre-update-18.0.0.bak`、
`omp.exe.stale-18.0.0.bak`；Windows 运行中进程持旧 inode（rename-aside 换入），
**当前运行中的 agent 宿主仍是旧二进制，下次重启宿主后 18.0.3 生效**。

## 追加:18.0.3 → 18.0.4(2026-08-24 深夜)

同路径升级:GitHub release `v18.0.4/omp-windows-x64.exe`,SHA-256
`8e04c83f…a2f47` 与官方 `SHA256SUMS.txt` 逐字符一致;备份
`omp.exe.pre-update-18.0.3.bak`,rename-aside 换入(运行中宿主持旧句柄)。

18.0.4 关键变更(与本环境痛点相关):
- **pi-agent-core:append-only 上下文模式序列化记忆化**——每轮同步开销不再
  随会话长度增长(直接利好 636K 级长会话);
- 修复终端工具结果结束的回合跳过 `onTurnEnd`(子代理收尾路径);
- pi-ai:OpenAI 兼容网关 Cursor 工具调用参数丢失修复(#9479)、413 分类改进。

验证:`omp --version` → 18.0.4;`omp models` exit 0;SOTA 扩展 repo/live
SHA parity `58607dc5…` 不受影响;扩展单测 25/25;路由门禁 39/39。
**生效时机同前:当前运行中宿主仍是 18.0.3,下次重启宿主后 18.0.4 生效。**
git-bash cp 对刚下载的 150MB exe 报 cannot stat(疑似 Defender 扫描锁),
PowerShell Copy-Item 成功——记为机构知识。

## 追加:18.0.4 → 18.0.5(2026-08-25)

`omp update` 只更新了 bun 包（18.0.5）并自曝 exe 仍 18.0.4——与 08-23 同一
行为：updater 不覆盖手工换入的 exe。同路径手工升级：release
`v18.0.5/omp-windows-x64.exe`，SHA-256 `921e74f2…92d6ec` 与官方一致；备份
`omp.exe.pre-update-18.0.4.bak` + rename-aside 换入。验证套件全绿：
`--version`=18.0.5、`omp models` exit 0、SOTA 扩展 parity `58607dc5…` +
25/25、路由门禁 OK。生效时机：运行中宿主下次重启后切到 18.0.5。

## 追加:自动升级机制(2026-08-26)

`omp update` 无法覆盖手工换入的 exe（两次升级实证），故自建计划任务
`OMP AutoUpdate`（每日 09:40，`StartWhenAvailable`）跑
`~/.omp/omp-autoupdate/omp-autoupdate.ps1`：

1. `releases.atom` 取最新 tag+发布时间（一次请求，免 GitHub API 限流）；
2. 已最新即退；**成熟期门槛：发布满 2 天才采用**（避开首发回归）；
3. 下载 exe+SHA256SUMS → SHA 校验 → 备份 `.pre-update-<旧版>.bak`（留 3 个）
   → rename-aside 换入；
4. 版本复核不符即回滚；全程日志 `autoupdate.log`，失败不动现有二进制。

两个机构知识：
- **PS5.1 读无 BOM 的 UTF-8 .ps1 会按 GBK 解析**——中文注释导致逻辑静默
  异常（版本比较被跳过、格式化输出残缺）。.ps1 必须带 BOM 写出
  （`encoding='utf-8-sig'`）。
- 版本比较前必须把外部命令输出**强制标量化**
  （`| Select-Object -First 1`），否则 `-le` 对集合返回集合，`if` 恒真。

生效时机不变：换入即对下次宿主启动生效。手动触发：
`Start-ScheduledTask -TaskName 'OMP AutoUpdate'`；日志
`~/.omp/omp-autoupdate/autoupdate.log`。

## 追加:18.0.5 → 18.0.6(2026-08-26)

v18.0.6 当日发布，自动升级器按 2 天成熟期正确拦下（`held back: age=0.0d`）；
用户明确要求立即升级，手动走同一验证路径：release `v18.0.6/omp-windows-x64.exe`
SHA-256 `9f458a9b…d71e2` 与官方一致；备份 `omp.exe.pre-update-18.0.5.bak` +
rename-aside 换入。验证套件全绿：`--version`=18.0.6、`omp models` exit 0、
SOTA parity `58607dc5…` + 25/25、路由门禁 OK。生效时机同前（下次宿主重启）。

注：成熟期门槛只约束自动升级器；用户显式要求时走手动路径可即时采用，
风险自担。

## 追加:18.0.6 → 18.0.7(2026-08-27 深夜)

v18.0.7 当日发布(age 0.16d)，自动升级器按 2 天成熟期正确拦下；用户明确要求
最新版，手动走同一验证路径(同 18.0.6 先例，风险自担)。

release `v18.0.7/omp-windows-x64.exe`，SHA-256 `f7de69c5…76f3a8` 与官方
`SHA256SUMS.txt` 一致；备份 `omp.exe.pre-update-18.0.6.bak` + rename-aside 换入。
验证套件全绿：`--version`=18.0.7、`omp models` exit 0、SOTA parity
`58607dc5…`、扩展单测 25/25、路由门禁 39/39。

18.0.7 关键变更(与本环境相关)：
- pi-ai：OpenAI 兼容网关流式错误不再被报成"空成功"——队列准入失败现在触发
  重试与模型回退(直接利好本地 NewAPI 429/503 场景)；
- pi-agent-core：Codex 远程 compaction 保留图像读取工具返回的图片，不再重放
  成错误合成用户消息；
- 新增按应用用量归因(`OMP_APP_NAME`，默认 omp)；Anthropic 订阅 OAuth 修复(#9801)。

机构知识(新)：**Invoke-WebRequest 下载 150MB 在本链路 ~120KB/s，900s 超时必挂**；
断点续传配方=`curl.exe -C - --retry 3 -L -o`。脚本
`~/.omp/omp-autoupdate/manual-update-18.0.7-resume.ps1`(首版 IWR 脚本超时后
弃用)。自动升级器仍用 IWR 且 TimeoutSec 900——若日后链路速度不改善，自动路径
也会超时失败(失败不动现有二进制，安全)，届时需给 autoupdate 脚本换 curl 续传。

生效时机同前：当前运行中宿主仍是 18.0.6，下次重启宿主后 18.0.7 生效。

### 换入后取证(反驳"未换入"告警)

换入后收到"磁盘显示未换入"的告警，实测逐项反证，不可只凭目录列举下结论：

- `Get-Command omp` → 唯一解析到 `~/.bun/bin/omp.exe`(无第二处安装/无 shim 混淆)；
- `omp.exe` mtime `2026/8/27 23:35:37`(换入时刻)，size 152232960；
- `sha256(omp.exe)` = `f7de69c5…76f3a8`，与官方发布哈希**逐字节一致**；
- 新进程 `& omp.exe --version` → `omp/18.0.7`；
- 备份链在位：`pre-update-18.0.6.bak` + `running-18.0.6.hold`(keep-3 保留 18.0.5/18.0.6)。

**新发现:OMP 自带内置 updater 也在抢同一路径。** `~/.bun/bin/` 出现
`omp.exe.<epochms>.<pid>.0.new` 分片(21:07 的 149431808B、22:40 的 6576704B)，
其 PID 9504/22080 均已不在运行列表 → 下载中途死掉的孤儿，已清理。

风险与结论:内置 updater 与本地 rename-aside 脚本共享 `~/.bun/bin/omp.exe`，
存在换入争用。判定顺序固定为 **哈希 > mtime > 目录列举**：只有
`sha256 == 发布哈希` 且新进程 `--version` 对得上才算换入成功；见到 `*.new`
分片先查持有进程，无进程即孤儿，直接删除，不要据此推断主二进制被回滚。
