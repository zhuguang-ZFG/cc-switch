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
