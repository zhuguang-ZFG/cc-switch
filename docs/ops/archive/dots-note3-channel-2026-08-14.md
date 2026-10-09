# dots-note3 渠道接入（2026-08-14）

在本地 NewAPI（127.0.0.1:3002）新增 **ch77 `dots-note3`**，上游 `https://note3-prev-api.askdiandian.com`（**Dots 点点官方平台预览 API**，非中转）。key 由用户提供，单 key（`ak_` 前缀，Dots 平台格式）。

## 渠道参数

| 项 | 值 |
|----|----|
| id / name | 77 / `dots-note3` |
| type | 1（OpenAI；仅 chat completions 可用，见下） |
| base_url | `https://note3-prev-api.askdiandian.com`（根路径，NewAPI 自动拼 `/v1/chat/completions`；勿带 `/v1`，见 newapi-audit 双 /v1 坑） |
| key | 单 key（见 NewAPI 渠道配置，本文档不落 key） |
| models | `dots-3-note-prev` |
| model_mapping | 无（原生模型名直挂，无别名） |
| priority / weight | 50 / 5（新源小权重，同 seekai/sotamodel 先例） |
| status | 1 enabled，auto_ban=1（guardian 故障自动禁用/恢复路径） |

## 上游特征（接入前直连实测）

- **平台身份**：`governance.dots_platform_key_not_allowed` / `governance.missing_api_key` 错误族 + `traceId` 字段 → Dots 官方平台治理层，非 NewAPI 系中转；域名过阿里云 WAF（`acw_tc` cookie）。
- `/v1/models`：200，**无需 key** 即返回模型列表，仅 1 个模型 `dots-3-note-prev`（真 key 下相同）。
- 鉴权边界：无 key → 401 `governance.missing_api_key`；无效 key → 403 `governance.dots_platform_key_not_allowed`。
- `/v1/chat/completions`：
  - ✅ 非流式 200（TTFT ~0.6s），`reasoning_content` 正常返回（**推理模型**，与 deepseek-v4-pro 池同款形态），`content` 正文正常、`finish_reason: stop`
  - ✅ 流式 200（SSE `delta.reasoning_content` 增量 + 正文 chunk，~1.3s 完成）
- ❌ `/v1/messages`（Anthropic 端点，含 `anthropic-version` 头）：400 `provider.client_bad_request`——该预览 API **不支持 Anthropic 端点**
- ❌ `/v1/responses`（标准 Responses 格式）：400 `provider.client_bad_request`——**不支持 Responses 端点**
- 端点存在性（假 key 探测）：`/v1/chat/completions`、`/v1/responses`、`/v1/messages`、`/v1/embeddings` 路由均存在（治理层 403 而非 404）。
- 出口：远程 IP 198.18.0.82（本机代理 fake-ip 保留段），TLS 0.76s。

## 验证

- NewAPI 渠道测试：`GET /api/channel/test/77?model=dots-3-note-prev` → success，**0.46s**
- 网关 e2e（`newapi_probe_key` 走 127.0.0.1:3002）：`dots-3-note-prev` → 200，`content:"OK"`、finish stop、usage 16/35/51
- 消费日志归因：`/api/log/?model_name=dots-3-note-prev` 2 条（渠道测试 + e2e），ch77 为唯一承载渠道

## 备注

- 创建走 fork 标准路径：`POST /api/channel/` body `{"mode":"single","channel":{...}}`（与 seekai/tabitoken 同款 fork API 约束）。
- 消费方约束：仅 OpenAI chat completions（流式/非流式）。Claude Code / Anthropic 格式请求无法经该渠道直通（NewAPI 转换层可转 OpenAI 格式，但上游 `/v1/messages` 本身 400——Claude Code 使用需经 NewAPI 的模型转换路径，未实测）。
- 预览模型 `dots-3-note-prev` 为推理模型：consumption 计费按默认 model ratio（两条小请求 quota 1050/1913，默认档）。
- 权重策略：新源小权重（5）；预览 API 稳定性未知，观察 guardian 慢响应/错误计数后再考虑调权。
- key 不落 repo/文档，仅存 NewAPI 渠道配置（同 seekai/sotamodel 惯例）。

## 回滚

- 渠道：`POST /api/channel/77/status {"status":2}`（与 guardian.py `disable_channel` 同款），或 `DELETE /api/channel/77` 彻底移除。
