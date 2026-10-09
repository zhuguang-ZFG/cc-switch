# OMP 语义 TTFT 网关：故障复盘与维护契约（2026-08-06）

**状态：** 已修复并部署
**范围：** `scripts/ops/omp-ttft-gateway.cjs`、`scripts/ops/test_omp_ttft_gateway.cjs`、OMP `zg-newapi-anthropic` 路由和用户级生产副本

## 1. 问题与根因

原生 OMP 首事件 watchdog 无法约束“首个可见 token”：Anthropic SSE 会先发送 `ping`、`message_start` 或 thinking，连接保持活跃但用户仍可能长时间看不到 text/tool 输出。因此增加 loopback 语义 TTFT 网关，在可见输出前缓存 SSE，超时后返回 504 触发 OMP fallback。

初版网关又暴露两类缺陷：

1. **事件终态竞态（隐式状态假设 + 测试缺口）。** thinking-only SSE 超过缓冲上限时，旧代码销毁上游并尝试返回 504，但未设置共享终态。`upstreamRes.destroy()` 后仍可能进入 `end` handler，后者再次 `commit()`，最终把失败覆盖成 HTTP 200，并返回截断的 thinking 流。
2. **超时边界不完整。** semantic timer 只在收到上游响应头后创建。上游若接受 TCP 连接但不发送响应头，请求不受 60 秒门限约束。

根因分类：

- D：测试覆盖缺口——只测 keepalive 超时、文本成功、非 SSE 透传；
- E：隐式假设——假设 destroy 后不会再出现可写事件；
- C：变更传播——仓库实现与 `~/.omp/guardian/` 生产副本必须同步。

## 2. 当前运行契约

| 边界         |           默认值 | 结果                            |
| ------------ | ---------------: | ------------------------------- |
| 监听地址     | `127.0.0.1:3003` | 仅本机 OMP 使用                 |
| 上游地址     | `127.0.0.1:3002` | NewAPI                          |
| 响应头门限   |         60000 ms | 超时返回 504 `overloaded_error` |
| 可见输出门限 |         60000 ms | 2xx SSE 无 text/tool 时返回 504 |
| 预提交缓冲   |            1 MiB | 超限返回 504，禁止截断 200      |

语义事件：

- **不提交：** `ping`、`message_start`、usage、thinking-only delta；
- **提交：** text delta、tool name、tool partial JSON；
- **透明透传：** 非 SSE 响应和非 2xx 响应。

OMP provider 必须满足：

- `zg-newapi-anthropic.baseUrl = http://127.0.0.1:3003`；
- `api = anthropic-messages`；
- `apiKey != PROXY_MANAGED`。

## 3. 状态机规则

每个请求只能有一个 terminal outcome：

```text
pending headers
  ├─ header timeout/error ──> failed (504/502)
  └─ response headers
       ├─ non-SSE/non-2xx ──> passthrough
       └─ 2xx SSE
            ├─ text/tool ───> committed 200
            ├─ semantic timeout ─> failed 504
            ├─ buffer overflow ──> failed 504
            └─ upstream error ───> failed 502
```

失败路径必须先设置 terminal，再销毁流。`data`、`end`、timeout、error、client close handler 都必须先检查 terminal；不能把“调用过 `res.end()`”当成状态机。

## 4. 实现经验

1. **destroy 不是终态。** Node 流销毁后仍可能触发 `end`、`error` 或 `close`；所有 handler 必须共享显式状态。
2. **超时按阶段定义。** TCP/响应头、首个语义输出、提交后流空闲是不同阶段，不能用一个在 `response` 后创建的 timer 冒充全链路 deadline。
3. **thinking 不等于用户可见输出。** high-thinking 请求可能先持续产生 thinking；门限应以 text/tool 为准，同时选择足以容纳正常推理的时间预算。
4. **分块解码必须保留 UTF-8 边界。** 使用 `StringDecoder`，不能对每个 chunk 独立 `toString("utf8")`。
5. **提交后尊重背压。** `res.write()` 返回 `false` 时暂停上游，在 `drain` 后恢复，避免慢客户端导致无界内存增长。
6. **运行时副本是第二交付面。** 仓库测试通过不代表生产已更新；必须同步用户级副本、精确回收 gateway 进程，并让唯一 supervisor 拉起新版本。
7. **测试计数会漂移。** 文档应记录验证命令和当前结果；新增/删除测试后，搜索并更新旧的 `N/N` 说法。

## 5. 必测矩阵

`node scripts/ops/test_omp_ttft_gateway.cjs` 必须覆盖：

- thinking delta 被判为非语义；
- keepalive/无可见输出返回 504；
- 缓冲溢出返回 504，绝不能返回截断 200；
- 上游不返回响应头时返回 504；
- text delta 到达后提交原始 SSE；
- 非 SSE 响应透明透传。

路由门禁：

```text
py -m unittest scripts.ops.test_omp_routes
```

现场验收：

1. 只有一个 `proxies-supervisor.py` owner；
2. `127.0.0.1:3003` 正在监听；
3. `GET http://127.0.0.1:3003/api/status` 返回 200；
4. 观察一次主路由成功或 OMP `retry_fallback_applied` → `retry_fallback_succeeded`。

## 6. 错误与正确模式

错误：

```js
upstreamRes.destroy();
writeGatewayError(res, 504, "overloaded_error", message);
// end handler 仍可能 commit 200。
```

正确：

```js
terminal = true;
writeGatewayError(res, 504, "overloaded_error", message);
upstreamReq.destroy();
upstreamRes.destroy();
// 后续 handler 看到 terminal 后立即返回。
```

## 7. 当前验证事实

- 网关协议回归：5/5 通过；
- OMP 路由门禁：7/7 通过；
- 周边 Guardian/route/smoke 回归：101/101 通过；
- 生产现场：3003 监听、`/api/status` HTTP 200、persistent supervisor 数量为 1。
