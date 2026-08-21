# OMP 交互会话"使用中退出"排查（2026-08-21）

症状：OMP 在 conhost 传统控制台里使用中突然退出回到命令行提示符，窗口不关，
用户未按 Ctrl+C。退出集中发生在 hutuji 仓会话（resume 后挂着
`eval` 跑 `scripts/agent_gate.py --profile hub` 期间）。

## 证据链

1. `~/.omp/logs/omp.YYYY-MM-DD.<pid>.log` 的 `Session exit recorded`：
   2026-08-21 全天 dispose×73（正常）、sighup×6（关窗口所致）、
   **sigint×4（15:57 / 16:28 / 16:30 / 16:32，均 pendingToolCalls=1）**、
   unhandled_rejection×1（08-20 EPIPE 写已关闭的 stdout，是终端消失的结果不是原因）。
2. OMP dist 中 `reason=sigint` 只由 `process.on("SIGINT")` 写入——即进程
   **真实收到了 Windows 控制台 Ctrl 事件**（CTRL_C/CTRL_BREAK，libuv 均映射
   为 SIGINT）。conhost 下窗口保留 + 回到 cmd 提示符，与"事件送达进程组、
   OMP 退出、cmd 仍在"完全吻合。
3. popup_hunter（`C:/Users/zhugu/popup_hunter.py`，只读 psutil 轮询，不干预
   任何窗口）日志显示：每次退出前后正值 **vpype 进程风暴**（pytest 层驱动，
   每秒数十个 vpype/python 生灭，pid 高速回收）。
4. 反常点：16:28/16:30 的风暴**不是**当时退出的 OMP 实例发起的（其实例的
   eval 尚未重跑）——是上一个死亡实例留下的孤儿 eval 内核仍在跑门禁。
5. 门禁自身（`D:/Users/hutuji/scripts/agent_gate.py` 及其 tests）经全仓检索
   **无任何 GenerateConsoleCtrlEvent/send_signal 调用**，只发 SIGKILL/
   TerminateJobObject，不是信号源。
6. Guardian/proxies-supervisor 不管理交互 OMP（supervisor-status.json 全绿、
   restarts=0），不是信号源。

## 机制判定（高置信度假定）

同族已修 bug：OMP CHANGELOG #7452（17.2.9）——Windows 下 bash 工具超时
取消沿 `th32ParentProcessID` 走进程树，pid 回收可把 OMP 误判为后代
TerminateProcess（无 session_exit 记录）。本机已装 17.2.10 含此修复，
但本次退出**有** sigint 记录，属同族剩余变种：

> OMP/Bun 收割超时/孤儿 eval 子进程时向进程组发控制台 Ctrl 事件，而
> Windows 控制台事件是**控制台范围内广播**——同控制台的 OMP 主进程一并
> 收到 SIGINT，被误当作用户 Ctrl+C，于是退出。vpype 风暴的 pid 回收
> 放大了误伤概率。

上游 issue #7452: https://github.com/can1357/oh-my-pi/issues/7452

## 规避（当前生效口径）

- **不要在 OMP 的 eval/bash 里跑 hutuji 门禁**（分钟级 + vpype 进程风暴）。
  独立窗口手动跑 `python scripts\agent_gate.py --profile hub`，OMP 会话里
  只读 `results/hutuji_gate_last.{json,md}` 报告，不损失信息。
- resume hutuji 会话时对 pending 的 eval 调用选**放弃**而不是重跑。
- 其他会话无 vpype 级进程 churn，不受影响。

## 验证（定性坐实方法）

下次 resume hutuji 会话、放弃 pending eval 后若不再退出，即坐实上述机制；
若仍退出，需重开排查（届时在退出瞬间抓 `Get-CimInstance Win32_Process`
快照，找同控制台上的发送者）。

## 待办

- 可选：持本 runbook 证据链给 oh-my-pi 提 issue（控制台事件广播误伤，
  #7452 修复未覆盖的路径）。
- sighup×6 是关窗口/关标签页的正常挂断，与本案无关；OMP 无守护模式，
  关窗即死，长任务别直接关窗（先 `/exit`）。
