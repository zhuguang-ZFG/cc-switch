# jianzhile.vip 渠道接入尝试（2026-08-13，未接入）

用户提供 `https://jianzhile.vip` + 单 key，要求测试后加入本地 NewAPI（127.0.0.1:3002）。

## 结论：未接入

上游测试**失败**——key 鉴权有效（`/v1/models` 200），但唯一暴露模型 `gpt-5.6-sol` 在所有端点/模式下**确定性 403**（下游拒绝）。注册会产生必死渠道，guardian auto_ban 会反复禁用，污染渠道池，故不建渠道。

## 上游特征（实测）

- 中继身份：`x-new-api-version: local-0.0.29`、`x-oneapi-request-id` 头 → **jianzhile.vip 本身是 NewAPI fork 中转**。
- `/v1/models`：200，仅 1 个模型 `["gpt-5.6-sol"]`（key 可见范围）。
- `/v1/chat/completions`（非流式/流式，max_tokens 16，3 次重试）：
  `403 {"error":{"message":"bad response status code 403 ...","code":"bad_response_status_code"}}`
- `/v1/messages`（Anthropic 端点，gpt-5.6-sol）：403 `bad_response_status_code`。
- 其他模型 id（`gpt-5.6-sol-max`、`claude-opus-5`、`deepseek-v4-flash` 等）：
  `503 model_not_found ... under group GPT (distributor)` → 该中转仅此一个可路由模型，且其下游 403。
- 鉴权边界确认：无 key / `x-api-key` → 401 `Invalid token`；Bearer key → 通过鉴权。403 来自中转的**下游渠道**，非 key 权限问题。
- 排除本机出口因素：本机无 Tailscale 适配器，NewAPI 生产出口与本探针同一公网 IP，403 非区域差异。

## 判定

- 403 非瞬态（3 连测同码、多端点一致），按「首调超时/瞬断可重试」标准不可救。
- 不满足接入门槛（sotamodel/seekai 均以直连 200 为前提），**不建渠道、不动 NewAPI**。

## 复测清单（上游侧修复后）

1. 与 jianzhile.vip 提供方确认：key 余额/配额、`gpt-5.6-sol` 在该中转的渠道是否 enabled、下游是否限制地域/额度。
2. 复测命令（bash，key 用环境变量不落盘）：
   `curl -s https://jianzhile.vip/v1/chat/completions -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{"model":"gpt-5.6-sol","messages":[{"role":"user","content":"Say OK"}],"max_tokens":16}'`
   需返回 200 且含 choices 内容。
3. 通过后再走标准路径：`POST /api/channel/` `{"mode":"single","channel":{...}}`（type 1，models `gpt-5.6-sol`，weight 5，auto_ban=1），`GET /api/channel/test/{id}?model=gpt-5.6-sol` 验证，按 seekai 渠道文档格式补 runbook。

## 相关

- 渠道接入先例：`seekai-channel-2026-08-11.md`、`sotamodel-channel-2026-08-11.md`
- 运维边界：`do-not-modify-cc-switch.md`（NewAPI 渠道变更允许，本次未变更）
