# AtomCode 本地代理故障根因与恢复（2026-08-04）

## 现象

- OMP 告警：`Warning: Fallback: atomcode/deepseek-v4-flash:high -> zg-newapi/gpt-5.6-sol`
- `curl 127.0.0.1:9457` 连接拒绝（exit 7），`atomgit-opencode-bridge` 无 `proxy.js` 进程。

## 根因（两层）

1. **AtomCode 自身 watchdog 无鉴权判死循环**（主因）：
   - `watchdog.js` 用 `GET /v1/usage` 探测存活，但该请求没有带本地 Bearer key。
   - `proxy.js` 在 `LOCAL_API_KEY` 未配置时对**所有请求 fail-closed 返回 401**（含 `/v1/usage`）。
   - 结果：watchdog 永远判死，每 30s 拉一个 `node proxy.js`；子进程因无 `LOCAL_API_KEY` 又被判死，`bridge.log` 从 12:51 到 13:54 每分钟刷 `bridge dead, restarting...`，且从未真正存活。
2. **Guardian 兜底失效**：
   - Guardian 心跳停在 13:56:33（PID 14776），进程随后消失；`watchdog.ps1` 从 10:58 起每 45s 报 `Heartbeat stale but pid=13912 not a python/guardian.py process (skip)`，但 Guardian 实际已不在，最终没有任何一方把它拉起。

## 修复

1. `watchdog.js`（commit `cf6aadb`，本机）：
   - 从 `~/.omp/guardian/secrets.json` 读取 `atomcode_proxy_key`；
   - 探测改为带 Bearer 的 `GET /v1/models`（本地常量，不触发上游，区分「进程存活」与「上游推理健康」）；
   - `startBridge` 通过 `env.LOCAL_API_KEY` 传给子进程；
   - 密钥缺失时拒绝拉起子进程并记日志。
2. 现场恢复：
   - 用 secrets 中的 key 拉起 `node proxy.js`（9457 恢复）；
   - Guardian 用 `start.bat` 同款 Python 重新拉起（PID 22096，心跳 17:05:29 恢复）；
   - Guardian 周期恢复（36.8s，阈值 30s 内）。

## 验证

- 受控停止 9457 后，新 watchdog 在 30s 窗口内只拉起 1 个 `node proxy.js`（进程树 1 watchdog + 1 proxy）。
- `GET /v1/models` 带 key 200，`deepseek-v4-flash` 真实请求 200，`finish_reason=stop`，内容 `ATOM_OK`。
- 早期 16-token 冒烟出现空 `content` 是 `max_tokens` 全被 reasoning 消耗（`finish_reason=length`），不是代理故障。

## 遗留

- 两处本地提交推送均 403（凭据 `zhuguang-ZFG` 对 `Small-tailqwq/atomgit-opencode-bridge` 与 `fifasheng-tech/catpaw-bridge` 无 push 权限），需切换远端/凭据后推送：
  - `atomgit-opencode-bridge` master `cf6aadb`
  - `catpaw-bridge` main `d5e3802`（另含 HTTP/工具/上下文/日志安全加固）
- Telegram 代理 `127.0.0.1:7897` 拒连（WinError 10061），Guardian 告警/远程命令通道不可用；推理与自愈主循环不受影响。
- 本机 `watchdog.lock` 内容为 PID 10880（已不存在的进程）；新 watchdog 已按锁接管逻辑覆盖，无需清理。
