# 本地 NewAPI / Guardian 运维记录（2026-08-05 下午）

记录两件事：Claude 渠道路由漂移修正，以及 agentrouter-proxy（8788）宕机与监管链断裂的排查修复。

## Claude 渠道路由修正

发现 `claude-opus-5` 路由池偏离既定方案：渠道 9 `linxi-k40`（林夕，实测 39.6s）被启用并提到最高优先级 57，成了实际主路由——"Claude 聚合慢"的直接原因。按用户决定调整为：

| 渠道 | 调整 | 角色 |
|---|---|---|
| 3 baibei-100xlabs | pri 57 / w 20 | 主路由（实测 ~3-5s） |
| 45 agentrouter | pri 50 / w 10 | 次路由（8788 恢复后健康） |
| 9 linxi-k40 | pri 40 / w 2 | 降级备用 |
| 18 linxi-k40-opus5-backup | 不动（pri 40 / w 2） | 降级备用 |
| 27/57 gorouter ×2 | 保持手动禁用 | 403 预扣费额度不足 |

验证：真实 `claude-opus-5` 请求 200，failover 双向可用（3→45 与 45→3 都实测成功）。

## 8788 宕机与监管链断裂（根因：BOM）

时间线与根因链：

1. agentrouter-proxy 绑定 `100.83.32.95:8788`（secrets.json 的 `local_proxy_bind_host`），脚本与配置本身完好，前台手动运行正常。
2. 历史误杀：Guardian 旧版探针打 `127.0.0.1:8788` 而代理只绑 Tailscale IP，健康代理被判死、反复重启后熔断。现行 live 代码探针已整体禁用（`check_local_proxy` 直接返回健康），该路径不复存在——代价是本地代理挂了不再自动重启。
3. **直接根因**：`~/.omp/guardian/secrets.json` 被某工具重写为**带 UTF-8 BOM**（约 13:13）。`guardian.py` 用 `encoding="utf-8"` 读 JSON，BOM 使 `json.loads` 抛 `ValueError`，异常被静默吞掉后全部配置为空，启动即 `RuntimeError: Missing Guardian secrets`，start.bat 每 10s 崩溃重启。
4. 监管链随之全断：Guardian 起不来 → 无人管渠道；watchdog 因心跳 pid 指向死进程而良性 skip（这是设计行为，非 bug）。

修复动作：

- 剥掉 `secrets.json` 的 BOM（值原样保留）。
- `guardian.py` secrets 加载改为 `utf-8-sig`（BOM/无 BOM 均兼容），并加了注释说明坑源。
- 删除 `restart_newapi_container()` 中不可达的 SSH 旧代码块（远端 VPS NewAPI 已永久删除；含 `lima` 主机别名，上传 GitHub 前清掉）。
- 手动重启 agentrouter-proxy（pid 存活，`/health` 带 key 200 / 无 key 401）。
- 计划任务 `NewAPI Guardian` 的动作最终改为 `conhost.exe --headless "C:\Users\zhugu\.omp\guardian\start.bat"`，cmd 窗口不再弹出，任务状态保持 `Running`（conhost 作为任务动作持有整个进程树）。Guardian 存活以 `heartbeat.json` 和 `guardian.log` 为准。
  - 中间方案 `wscript.exe //B //nologo start-hidden.vbs` 被放弃，两个原因：① `Run(..., waitOnReturn=False)` 让 wscript 立即退出，任务结束后 Task Scheduler 把 start.bat + python 整棵树回收，Guardian 约 90 秒后死亡；② 自建的 `start-hidden.vbs` 在创建约 20 分钟后从磁盘消失（疑似安全软件查杀隐藏启动类 VBS）。若日后再用 VBS 方案，`waitOnReturn` 必须为 `True`。
- watchdog.ps1 一并拉起（mutex 防重复）。

验证：Guardian 心跳持续刷新；无误杀（`disabled_channels` 仍只有 gorouter 27/57，`restart_counts` 全 0）；`scripts/ops` 镜像已同步 live 并更新测试，89 个 unittest 全过。

## 踩坑备忘

- **BOM 坑**：PowerShell 5.1 `Out-File -Encoding utf8` / 部分编辑器写 JSON 会加 BOM；Python 侧读第三方维护的 JSON 一律用 `utf-8-sig`。Guardian 的 `_SECRETS` 加载把解析错误静默成空 dict，故障表现（"配置缺失"）与真实原因（"文件没读到"）隔了一层，排查时先 `od -c` 看文件头。
- **计划任务 RunLevel=Highest**：修改任务定义（`Set-ScheduledTask` / `schtasks /change`）需提权令牌；`schtasks /change` 还会交互式要密码。可用 `Start-Process powershell -Verb RunAs` 走 UAC。
- **Git Bash 路径转换**：`schtasks /change` 的 `/change` 会被 MSYS 转成 `C:/Program Files/Git/change`，需 `MSYS2_ARG_CONV_EXCL='*'`。
- **隐藏启动常驻 bat**：首选 `conhost.exe --headless <script>`（Windows 11 内置，无脚本文件、任务状态保持 `Running`）。若用 VBS 包装器（`wscript.exe //B //nologo`），`WScript.Shell.Run` 的 `waitOnReturn` 必须为 `True`——否则 wscript 退出即任务结束，Task Scheduler 会回收整个进程树。


## 2026-08-05 晚：Guardian/OMP 策略边界加固

- Guardian 恢复渠道时只恢复 `weight`；保留当前 `priority`，不再从 `weight_history` 回写人工路由优先级。
- 渠道恢复不再自动修改 OMP `config.yml` 的 `modelRoles`。OMP 角色只做端点观测和告警，路由策略由人工维护，避免恢复事件覆盖 `slow/plan/vision/default`。
- 生产运行镜像 `C:/Users/zhugu/.omp/guardian/guardian.py` 已与仓库 `scripts/ops/guardian.py` 同步，并重启 `NewAPI Guardian` 计划任务。
- 验证：Guardian + smoke 测试 98/98 通过；`py_compile` 通过；心跳 `heartbeat.json` 持续刷新。