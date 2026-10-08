# agentrouter 桥 400 根因修复 + DeepSeek 聚合收口（2026-10-08）

## 背景

OMP → 本地 NewAPI（`127.0.0.1:3002`）→ ch180 agentrouter（`ps.air-outer.com/v1`，
经 8788 桥 `~/.kimi-code/proxies/agentrouter-proxy/agentrouter-proxy.py` 转发至
`agentrouter.org/v1`）链路上，`deepseek-v4-flash` 间歇 400。
用户裁决：**不许禁用渠道消错误，必须找根因修复**（初版曾误禁 ch180，已回滚恢复入池）。

## 根因（抓包 `~/.omp/logs/http-400-requests/` 回放定位，三层叠加）

1. **NewAPI relay 重序列化**：`tools[].function.parameters` 缺失 `required` 时被重写为
   显式 `null`，严格后端 400 `null is not of type 'array'`。
2. **空串 reasoning_content 回传**：assistant 消息 `reasoning_content:""` 被上游拒
   （thinking 块必须回传且非空）。占位串演进：`""`→400、`"(elided)"`→content-blocked 400、
   `"thinking"`→200（最终采用）。
3. **key 侧缺陷与冷却连坐**：keys.json key3 确定性路由严格后端（同 payload 在 key1/2
   200），属 key 侧非请求侧；且旧桥逻辑任一网关 transient 即冷却整个 key，健康 key 被
   耗尽。key0 额度尽（402 quota）。

## 修复（仅桥侧，`agentrouter-proxy.py`；备份 `.bak-20261008-400sanitize`）

- `_sanitize_chat_payload()`：`required` 非 list → 补 `[]`；deepseek 系模型 assistant
  空 `reasoning_content` → 补 `"thinking"`；命中计数写日志 `⛨ sanitized N field(s)`。
- `_KEYSIDE_400_WORDS` + `_is_retryable` 400 分支：严格后端特征 400 归 keyside，冷却换 key。
- `_classify()`/`_forward()` 统一重试分层（chat + responses 两路径共享）：
  同一 key 顺序试完全部网关；transient 只换网关不冷却 key；全部网关 transient 才冷却；
  keyside 立即冷却换 key；fatal 快速失败。MAX_ATTEMPTS=4。

## 验证

- 生产形态回放（400 抓包原样重放经修复桥）：6/6 HTTP 200。
  注意自测两个假阳性教训：合成 tool_calls 字段是 `arguments` 非 `parameters`；
  回放体上追加重复工具名会自造 400。
- A/B/C 矩阵（key × 网关）+ burst 并发多轮全 200；NewAPI 端到端
  `deepseek-v4-flash` 6 并发 6/6 200（详见 `newapi-deepseek-v4-flash-pool-2026-10-08.md`）。
- ch180 恢复入池（status=1），主 51/备 30/末 25 优先级不变。

## muyuan（ch173）两模型处置

- `qwen3.8-flash-next`：models.yml L543 已入库（10-07 批次）；10-08 OMP 实弹
  `omp -p --model zg-newapi/qwen3.8-flash-next` **200 闭环**。
- `mistral-large-4-0`：用户点名入库 → models.yml 已加条目，但**站侧确定性 503**
  `No available channel for model ... (distributor)`（错误体来自 muyuan 自身路由层；
  同渠道对照组 mistral-code-latest 200，本地 channels/abilities enabled=1 健康；
  10-06 曾实弹 200、10-07 403 tier_not_allowed、10-08 503——站侧动态门，本地无可修缺陷）。
  条目注释标注"站侧503待恢复勿入主链"，站恢复即插即用。

## 回归

- `python3 -m unittest scripts.ops.test_omp_routes`：40/40 OK。
- `python3 scripts/ops/newapi-local-smoke.py`：3 FAIL 与存量基线一致
  （channel model isolation / fallback channel posture / primary opus pool posture），无新增。

## 待示下

- ~~keys.json key0（额度尽）/key3（严格后端）是否摘除~~
  **2026-10-08 20:01 处置**：逐 key × 双上游探针实锤 key0 = air-outer 403
  `user quota is not enough`（key1/2/3 同探 200；agentrouter.org 当日对四把全 503，
  站面另议）。已将 key0 移入 keys.json `keys_disabled`（带 reason/时间戳，桥只读
  `keys` 数组），备份 `keys.json.bak-20261008-200141-drop-key0`；mtime 热加载生效
  无需重启，`/health` 报 keys=3，NewAPI 端到端 `deepseek-v4-flash` 并发 4/4 200。
  key3（严格后端 keyside 400）暂留池——冷却换 key 逻辑已能确定性绕开，摘除与否待观察。
- 桥 sanitizer/重试改动是否镜像一份到 `scripts/ops/`（现仅生产文件）。
