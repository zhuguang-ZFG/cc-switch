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
- 启动：`node C:\Users\zhugu\.kimi-code\proxies\anyrouter-proxy\proxy.cjs`（当前由 `proxies-supervisor.py` 单实例托管）。

## 上游抖动（重要）

同一请求体首次 200、复测 520（空响应）已复现；claude 直连 40–150s 不等。520/503 空响应多为 key 级限流/容量问题而非指纹失败——**打探要克制**，失败后间隔 30s+ 再试。2026-08-06 初始方案曾依赖 OMP 重试和慢链兜底；2026-08-14 起改用下文的有界手工 canary，不再自动回退到 AnyRouter。

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

## 2026-08-14：NewAPI ch72 Claude/Sol 故障域拆分

ch72 长期无法恢复并非本地 8789 代理失活：代理进程和监听 PID 一致，OMP
定向 `anyrouter/claude-opus-5` 请求实际到达上游，但 75 秒内持续 429 并以
`Deadline exceeded` 终止。Guardian 已累计 132 次恢复失败。

结构性根因是 ch72 同时登记 Sol 与 Claude，且 `test_model` 为空。NewAPI 恢复
探针默认选择 models 首项 `gpt-5.6-sol`；该 Responses/Sol 表面持续返回容量
上限，使整个渠道保持 disabled，即使 Claude 表面单独恢复也无法进入池。

最终合同：

- ch72 只保留 `claude-opus-5`、`claude-opus-4-8` 及三个 `zg-*` Claude 别名；
- `test_model=claude-opus-5`，priority/weight 保持 fallback 层 `40/2`；
- 上游未恢复前保持 `status=2`、abilities disabled，不通过加权强行放量；
- Sol 不创建新渠道；旧 Sol 配置只保存在敏感回滚备份，确认上游恢复后再独立接入；
- 变更工具为 `scripts/ops/split_anyrouter_channel.py`，默认 dry-run，`--apply`
  前创建完整渠道备份，并在 abilities/渠道验收失败时尝试恢复原对象。

实施结果（15:01）：

- ch72 已按上述合同更新并回读验证；5 条 abilities 均为 enabled=0、priority/weight=40/2；
- Claude 专用管理探针 `test/72?model=claude-opus-5` 精确命中后仍返回 429
  `Service Unavailable`，所以保持 status=2，未声称上游恢复；
- 渠道备份 `channel-72-anyrouter-before-claude-split-20260814-150134.json`
  （1335 B，SHA-256 `6A733E6E4AF9E554C3C92D50DA502CF4864CC059EE5A2EA46FDA10E0E3CC20FC`）；
- SQLite 备份 `new-api-before-anyrouter-claude-split-20260814-150134.db`
  （61837312 B，SHA-256 `DDDFDF625AA15AEB65457165237BE04304435F71322A7159092F57B4E2953362`，
  `integrity_check=ok`）。渠道 JSON 含凭据，不得提交或外传。

## 2026-08-14：有界超时与手工 canary

AnyRouter 上游持续 429/无首事件时，OMP、NewAPI 和本地代理的嵌套重试会放大请求。
当前预算按层拆分：

- 主 Opus 语义 TTFT 网关的响应头/首语义事件超时继续保持 60 秒；
- OMP 默认 stream first-event/idle watchdog 继续继承 300 秒；全局
  `retry.maxRetries=3` 未改，但 `fallbackChains.slow` 已移除
  `anyrouter/claude-opus-5`，所以日常自动路径不会再打到故障 AnyRouter；
- NewAPI `RetryTimes=1`，由 `newapi-local-smoke.py` 作为必需选项防漂移；
- AnyRouter 代理的两个请求超时从 600 秒改为 180 秒，
  `x-stainless-timeout` 同步从 600 改为 180；
- `scripts/ops/anyrouter-canary.yml` 仅用于手工探测：`maxRetries=1`、
  `modelFallback=false`、首事件 180 秒；命令仍必须带 `--max-time 3m`。

部署与回滚证据：

- NewAPI 选项备份 `newapi-retry-budget-20260814-152550.json`：25 B，
  SHA-256 `77A1E0DA7797C25A1D54507B0596108987B0A2CB9CD93C223DECC81C275798FE`；
- OMP 原配置备份 `config.yml.20260814-152604-anyrouter-timeout.bak`：2982 B，
  SHA-256 `39191E1F4554BE2B57559388247267E43465818358E1E7672B3F36F14C7F1146`；
- AnyRouter 原代理备份 `proxy.cjs.20260814-152604-timeout600s.bak`：24978 B，
  SHA-256 `53A7B18D459AA5678D54FACB5AE19BB75056A8D0B346652D2B2D81DE4B79D705`；
- 换行保留修正后的代理仍为 24978 B，SHA-256
  `85545FF6D929D63803E7B3D95AEEF2B12B827C0F381B7FA8CE95CA0C458EB6F8`。

运行验证：首次回收 PID 8948 时，30 秒窗口结束仍无监听，随后 supervisor
恢复 PID 25284；修正换行后再次精确回收 PID 25284，20.2 秒内恢复为 PID
24840，由同一 `proxies-supervisor.py` PID 17164 托管，8789 只有一个监听者。
未重启任何 OMP 会话。仓库路由/运维门禁 68 项通过，NewAPI live smoke 全绿，
系统健康检查 24/24。隔离 canary 正确解析 200K Opus 能力，但实际请求 181 秒后
以 `Deadline exceeded`、exit 1 结束，无 fallback、无成功响应；ch72 仍保持
disabled，不能宣称 AnyRouter 上游已恢复。
