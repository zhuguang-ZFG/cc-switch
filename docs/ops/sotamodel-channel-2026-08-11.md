# sotamodel 渠道接入（2026-08-11）

在本地 NewAPI（127.0.0.1:3002）新增 sotamodel 双渠道：ch76 `sotamodel`（claude，type 14）与 ch77 `sotamodel-gpt`（gpt，type 1），上游 `https://www.sotamodel.net`（new-api 系中转，同时支持 OpenAI / Anthropic 端点）。key 由用户提供，单 key。

## ch76 `sotamodel`（claude）

| 项 | 值 |
|----|----|
| id / name | 76 / `sotamodel` |
| type | 14（Claude relay，与 ch3/ch9/ch75 一致） |
| base_url | `https://www.sotamodel.net` |
| key | 单 key（见 NewAPI 渠道配置，本文档不落 key） |
| models | `claude-opus-5, claude-opus-5-max, claude-opus-5-xhigh, zg-claude-opus-5` |
| model_mapping | `{"zg-claude-opus-5":"claude-opus-5"}` |
| priority / weight | 50 / 5（小权重入 opus 池，与 ch75 tabitoken w7 同级） |
| status | 1 enabled，auto_ban=1（guardian 故障自动禁用） |

## ch77 `sotamodel-gpt`（gpt）

| 项 | 值 |
|----|----|
| id / name | 77 / `sotamodel-gpt` |
| type | 1（OpenAI） |
| base_url | `https://www.sotamodel.net` |
| models | `gpt-5.6-sol, gpt-5.6-sol-max, gpt-5.6-sol-xhigh, zg-gpt-5.6-sol` |
| model_mapping | `{"zg-gpt-5.6-sol":"gpt-5.6-sol"}` |
| priority / weight | 50 / 5 |
| status | 1 enabled |

**关键约束：该上游 gpt 系列仅流式可用**——非流式 chat completions 返回空 SSE 流（`choices:[]`、0 completion token）；Anthropic `/v1/messages` 对 gpt 返回空 body。流式（`stream:true`）与 `/v1/responses` 正常。因此：

- gpt 不能挂 ch76（type 14 会转成 /v1/messages 转发，必死）→ 单列 type 1 渠道
- NewAPI 渠道测试走非流式，对 ch77 必报 `bad_response_body`——**这是探针形态不兼容，不是渠道故障**；该错误不匹配 guardian 的 ERROR_DISABLE_KEYWORDS，NewAPI 请求路径 auto_ban 也不触发（上游返回 200），渠道不会被自动禁用
- 非流式客户端经网关命中 ch77 会得到错误（200+SSE 体无法解析）；生产消费方（OMP / Claude Code）均为流式，不受影响

## 上游测试（接入前直连实测）

`/v1/models` 返回 10 个模型，实测结论：

- ✅ `claude-opus-5` / `claude-opus-5-max` / `claude-opus-5-xhigh`：OpenAI `/v1/chat/completions` 与 Anthropic `/v1/messages` 均 200（max/xhigh 上游实际映射到 opus-5，响应 model 字段回显 `claude-opus-5`）
- ⚠️ `gpt-5.6-sol` / `gpt-5.6-sol-max` / `gpt-5.6-sol-xhigh`：**仅流式可用**——非流式一律返回空 SSE 流（`choices:[]`、0 completion token，`stream:false` 无效）；`stream:true` 与 `/v1/responses` 正常返回（max/xhigh 上游映射到 sol，回显 `gpt-5.6-sol`）。初次接入时误判为死路由未注册，复核后建 ch77
- `model-A/T/O/S`：不透明别名，未测未注册

## 验证

- 直连：`POST /v1/chat/completions`（claude-opus-5）200；`POST /v1/messages`（claude-opus-5）200
- 直连：`POST /v1/chat/completions`（gpt-5.6-sol，`stream:true`）正常流式输出；`/v1/responses` 200
- NewAPI 渠道测试：`GET /api/channel/test/76?model=claude-opus-5` → success，4.75s
- ch77 渠道测试：`GET /api/channel/test/77?model=gpt-5.6-sol` → `bad_response_body`（预期，见上约束）
- 网关 e2e（用户令牌走 127.0.0.1:3002，`zg-gpt-5.6-sol`，流式 ×6）：全部 200，消费日志归因全部 ch77（`channel:77`、`is_stream:true`、`upstream_model_name:gpt-5.6-sol`）
- prompt caching（ch76）：直连 `/v1/messages` 带 `cache_control` 两次调用均报 `cache_read_input_tokens:1203`；经网关 `claude-opus-5-max`（仅 ch76 serving，强制路由）同样透传缓存用量，消费日志 `cache_tokens:1102`、缓存调用 quota 49875 < 未命中调用 60206——**缓存真实生效且 NewAPI 计费有折扣**。异常点：全新内容首调即报 cache_read（`cache_creation:0`），上游为多租户中转，cache 口径可能跨用户共享或为其自报，不可按 Anthropic 官方语义理解；个别调用日志 `cache_tokens:0`（缓存归因偶发丢失）

## 备注

- 创建走 fork 标准路径：`POST /api/channel/` body `{"mode":"single","channel":{...}}`（与 tabitoken 文档同款 fork API 约束）
- gpt 池现状（2026-08-11）：启用中 serving gpt-5.6-sol 的仅 ch73（w5）与 ch77（w5）
- 权重策略：新源小权重（5），如稳定性验证通过可再调；改纯备份则 weight 置 0

## 回滚

- 渠道：`POST /api/channel/76/status {"status":2}`、`POST /api/channel/77/status {"status":2}`（与 guardian.py `disable_channel` 同款）
