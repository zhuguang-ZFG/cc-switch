# agentrouter-proxy 安全/重试修复（2026-08-02）

对 `C:/Users/zhugu/.kimi-code/proxies/agentrouter-proxy/agentrouter-proxy.py` 的深度审查（三个审查代理并行：SecurityAudit / RetryAudit / RuntimeAudit）+ 修复。本机服务把 agentrouter（claude-opus-5/4-8）暴露为 OpenAI 兼容端点供 OMP/Kimi 使用（见 `agentrouter-local-proxy-2026-07-31.md`）。

## 1. 审查发现（修复前）

| 优先级 | 问题 | 影响 |
|---|---|---|
| **BLOCKER** | 运行态进程 `--host 0.0.0.0` 且未配 `--api-key` | LAN/Tailscale（IP 192.168.1.3/100.83.32.95 可观测）任意客户端可 POST 消耗上游 key；`/health` 泄露域名/key 数/UA；`/v1/models` 带 key 打上游 |
| **HIGH** | 500 在 `_is_retryable_status` 中（与 502/503/504/52x 并列） | 4 key × 2 域名 = **16 次上游调用**（模拟实测：mock 上游恒 500 → upstream_calls=16）；NewAPI 预扣费 per request，失败后仍计费 → 单请求最多 16x 配额消耗；500 几乎总是 per-request/model 故障，重试备份域 + 3 key 不可能成功 |
| **HIGH** | 重试无总时限 | 每次 300s read timeout × 16 次 + 每 key 冷却等待 ≤30s → 最坏 **~84 分钟**才返回 502；调用方超时先放弃后代理仍在烧配额 |
| **MED** | `raise HTTPException(detail=...)` | FastAPI 默认包 `{"detail":{...}}`，OpenAI 客户端读顶层 `error.message` → 上游错误体不可达（实测上游 400 到达客户端变 `{"detail":{...}}`） |
| **MED** | `keys.json` ACL 继承（644，CodexSandboxUsers 有 Modify） | 本地沙箱主体可读/替换凭证文件；且文件支持 mtime 热加载（改动即生效） |
| LOW | watchdog 启动未显式指定 host；无 watchdog 进程在跑 | 运行态 `0.0.0.0` 来自旧手动启动（PID 33948，08-01 01:39 起，源码 08-02 01:36 已改但进程未重启 → 修复未生效） |

## 2. 修复内容

### 2.1 500 不再重试（BLOCKER 级放大消除）

`_is_retryable_status` 从 `(500, 502, 503, 504, 520, 521, 522, 524)` 改为 `(502, 503, 504, 520, 521, 522, 524)`。

- 500 = per-request/model 级故障，重试只放大配额消耗（16x→1x）。
- 502/503/504/52x 保留重试（网关级故障，failover 到备份域合理）。

### 2.2 120s 总时限

循环入口加 `_DEADLINE_S = 120`，超时 break 并返回 502（`deadline exceeded`）。最坏 84min → 120s。

### 2.3 错误体顶层可达

- 新增 `@app.exception_handler(HTTPException)`：`detail` 含 `error` 键时展平到顶层。
- 4 处 `raise HTTPException(detail=_safe_err_raw(...))` → `return JSONResponse(status_code=..., content=_safe_err_raw(...))`（stream 非重试 / non-stream 非重试 / 终态 502 / 400 bad json / 503 no keys）。

### 2.4 绑定 127.0.0.1 + watchdog 显式 host

- 杀旧 PID 33948（`0.0.0.0`），新进程显式 `--host 127.0.0.1`（PID 13912 运行中）。
- `watchdog.ps1` 启动命令加 `--host 127.0.0.1`（防再次以 0.0.0.0 拉起）。

### 2.5 keys.json ACL 收紧

`icacls /inheritance:r /grant:r zhugu:(R,W)` —— 移除继承，仅当前用户读写。Windows 上 `chmod 600` 无效（NTFS 不遵循 Unix 位）。

## 3. 验证

```text
500 retryable? False（502/503 仍 True）
py_compile 通过
health=200；正常 claude-opus-5 请求 200（content OK）
错误体顶层：{"error":{"message":"bad json: ...","type":"invalid_request_error"}}（不再包 detail）
127.0.0.1:8788 LISTEN（PID 13912）；Tailscale 100.64.0.1:8788 不可达（000）
icacls 输出：已成功处理 1 个文件
```

## 4. 遗留可选

- NewAPI `AutomaticRetryStatusCodes` 仍含 `500-504`（NewAPI 层对 500 也会重试到下一渠道）。因 proxy 已不重试 500，影响有限；若要彻底可从重试状态码移除 500。
- watchdog.ps1 当前无进程在跑（RuntimeAudit 确认），代理靠手动启动。如需自动恢复需挂任务计划/启动项。

> 安全：本文档不含任何 API key。
