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
