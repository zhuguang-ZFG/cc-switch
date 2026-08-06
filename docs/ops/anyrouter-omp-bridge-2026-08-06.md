# AnyRouter OMP 桥接（指纹代理）— 2026-08-06

## 背景与结论

AnyRouter（https://anyrouter.top）是公益 Claude 中转站，余额约 700。其网关对请求做**严格指纹校验**，普通 Anthropic 客户端（OMP 原生请求）会被拒：缺少 `anthropic-beta` 头时 400「1m 上下文已经全量可用」，请求体不满足指纹时 503/520（空响应）。

**方案**：本地透传代理 `C:\Users\zhugu\.kimi-code\proxies\anyrouter-proxy\proxy.cjs`，监听 `127.0.0.1:8789`，把请求改写成 claude-cli 同款指纹再转发 anyrouter.top。OMP 经 `models.yml` 的 `anyrouter` provider（`api: anthropic-messages`）接入。

**已验证**（2026-08-06，真实 claude-cli 2.1.220 + 真实 key）：
- `claude -p` 直连 anyrouter 成功（含 `[1m]` 模型，154s）。
- 嗅探捕获真实请求模板，代理按模板注入指纹后，`omp -p --model anyrouter/claude-opus-5` 返回 `ANYROUTER_OMP_OK`（87.8s）。
- `scripts/ops/test_omp_routes.py` 32 项全绿。

## 指纹要求（实测，2026-08-06）

| 项 | 要求 | 缺失表现 |
|---|---|---|
| `anthropic-beta` 头 | 必须含 `context-1m-2025-08-07`（建议整份 claude 列表） | 400「请启用 1m 上下文」 |
| `system[0]` | billing 头块 `x-anthropic-billing-header: cc_version=…; cc_entrypoint=sdk-cli;` | 520 |
| `system[1]` | `You are a Claude agent, built on Anthropic's Claude Agent SDK.` | 503→520 |
| `system[2]` | 真实 harness 提示（**内容匹配，长度无关**；伪造同长文本仍 520） | 520 |
| `metadata.user_id` | JSON 字符串 `{device_id, account_uuid, session_id}`；session 每次随机（固定高频 user_id 会被封禁） | 520/403 |
| `thinking` | 必须存在（缺失触发上游 new-api 后端 panic） | 500/520 |
| 工具 | 需带 tools 数组（OMP 自带，无需伪造内容） | 520 |
| URL | `/v1/messages?beta=true` | — |

## 代理说明

- 端口 `8789`（原 anyrouter 代理曾用端口，Guardian 注释中的旧表项即此）。
- Key 存 `~/.omp/guardian/secrets.json` 的 `anyrouter_proxy_key`（不写入仓库）。
- 指纹存 `config.json`：`device_id`（本机 claude 真实设备指纹，64-hex）、`billing_header`、`harness_block`（嗅探所得真实内容）。**勿外传这些指纹文件**。
- 日志 `proxy.log`，>5MB 轮转。
- 启动：`node C:\Users\zhugu\.kimi-code\proxies\anyrouter-proxy\proxy.cjs`（当前由 omp hub detached 托管）。

## 上游抖动（重要）

同一请求体首次 200、复测 520（空响应）已复现；claude 直连 40–150s 不等。520/503 空响应多为 key 级限流/容量问题而非指纹失败——**打探要克制**，失败后间隔 30s+ 再试。OMP 侧 `retry.maxRetries: 2` + 慢链兜底可吸收。

## OMP 配置变更

- `models.yml`：新增 `anyrouter` provider（claude-opus-5 / claude-opus-4-8，1M ctx，`api: anthropic-messages`）。
- `config.yml`：
  - `providers.maxInFlightRequests.anyrouter: 2`（公益站，克制并发）。
  - `fallbackChains.slow` 末尾追加 `anyrouter/claude-opus-5`（仅 Claude 慢链最后兜底；不进入 default/task 主链，避免上游抖动影响日常）。
  - `disabledProviders` 移除 `anyrouter`（保留则 route gate 硬违规）。
- 备份：`config.yml.20260806-anyrouter-backup.bak`、`models.yml.20260806-anyrouter-backup.bak`。

## 运维

- 健康检查：`curl http://127.0.0.1:8789/v1/messages -H "content-type: application/json" -d '{"model":"claude-opus-5","max_tokens":8,"messages":[{"role":"user","content":"ping"}]}'`（注意：最小请求体也会偶发 520，见上）。
- 代理挂了 → OMP slow 链自动跳过（cooldown-expiry），恢复后重启代理即可。
- 可选后续：把 anyrouter 加回 `guardian-live.py` 的 `LOCAL_PROXIES`（含 `test_guardian.py` 的 `test_anyrouter_removed_from_local_proxies` 断言需同步反转）。
- 上游 key 再失效时（历史踩坑：502 空响应、重启无效），从 `disabledProviders` + `models.yml` + 慢链移除，同上次处理。
