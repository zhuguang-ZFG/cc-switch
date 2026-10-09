# seekai 渠道接入（2026-08-11）

在本地 NewAPI（127.0.0.1:3002）新增 **ch78 `seekai`**，上游 `https://seekai.cc`（new-api 系中转，OpenAI / Anthropic 端点均可用）。key 由用户提供，单 key。

## 渠道参数

| 项 | 值 |
|----|----|
| id / name | 78 / `seekai` |
| type | 1（OpenAI；claude 由 NewAPI 转换，同 agentrouter 先例） |
| base_url | `https://seekai.cc` |
| key | 单 key（见 NewAPI 渠道配置，本文档不落 key） |
| models | `claude-opus-5, claude-opus-4-8, claude-fable-5, gpt-5.6-sol, deepseek-v4-flash, zg-claude-opus-5, zg-gpt-5.6-sol` |
| model_mapping | `{"zg-claude-opus-5":"claude-opus-5","zg-gpt-5.6-sol":"gpt-5.6-sol"}` |
| priority / weight | 50 / 5（默认层小权重） |
| status | 1 enabled，auto_ban=1 |

## 上游测试（接入前直连实测）

`/v1/models` 返回 19 个模型。与 sotamodel 不同：**gpt-5.6-sol 非流式也正常**，全模型双模式可用：

- ✅ `claude-opus-5`：chat completions 非流式 200；`/v1/messages` 200
- ✅ `claude-opus-4-8`：200（首次调用超时，重试通过——上游冷启动慢）
- ✅ `claude-fable-5`：200
- ✅ `gpt-5.6-sol`：非流式 200、流式 200（首次流式超时，重试通过）
- ✅ `deepseek-v4-flash`：首次 500 `do_request_failed`，重试 200
- 未测未注册：`gpt-5-5/5-4/5.6/5-6-terra/5-6-luna`、`gemini-3-flash/3-pro/3-1-pro`、`glm-5-2`、`grok-4-5`、`claude-sonnet-5`、`deepseek-v4-pro`、`DeepSeek-V4-Flash-0731`——上游首调超时/瞬断较常见，注册前需逐一复测

**上游稳定性特征**：首调冷启动慢（27s 渠道测试）且偶发 500/超时，重试即恢复。guardian 瞬态判定不受影响（500 在 NewAPI 重试码 500-503 内，会池内重试）。

## 验证

- NewAPI 渠道测试：`GET /api/channel/test/78?model=claude-opus-5` → success，27.1s；`?model=gpt-5.6-sol` → success，7.2s（非流式测试通过，与 sotamodel ch77 探针不兼容情形相反）

## 备注

- 创建走 fork 标准路径：`POST /api/channel/` body `{"mode":"single","channel":{...}}`
- 权重策略：新源小权重（5）；上游冷启动慢，观察 guardian 慢响应计数后再考虑调权
- 与 sotamodel 互补：sotamodel gpt 仅流式可用（ch77），seekai gpt 双模式可用（ch78）——非流式客户端的 gpt-5.6-sol 流量应优先落 ch78

## 回滚

- 渠道：`POST /api/channel/78/status {"status":2}`（与 guardian.py `disable_channel` 同款）
