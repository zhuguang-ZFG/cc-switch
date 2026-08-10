# sotamodel 渠道接入（2026-08-11）

在本地 NewAPI（127.0.0.1:3002）新增 **ch76 `sotamodel`** 渠道，上游 `https://www.sotamodel.net`（new-api 系中转，同时支持 OpenAI / Anthropic 端点）。key 由用户提供，单 key。

## 渠道参数

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

## 上游测试（接入前直连实测）

`/v1/models` 返回 10 个模型，实测结论：

- ✅ `claude-opus-5` / `claude-opus-5-max` / `claude-opus-5-xhigh`：OpenAI `/v1/chat/completions` 与 Anthropic `/v1/messages` 均 200（max/xhigh 上游实际映射到 opus-5，响应 model 字段回显 `claude-opus-5`）
- ❌ `gpt-5.6-sol` / `gpt-5.6-sol-max` / `gpt-5.6-sol-xhigh`：**上游死路由**——所有请求返回空 SSE 流（`choices:[]`、0 completion token），`stream:false` 无效。未注册进渠道，避免污染 gpt 生产池
- `model-A/T/O/S`：不透明别名，未测未注册

## 验证

- 直连：`POST /v1/chat/completions`（claude-opus-5）200；`POST /v1/messages`（claude-opus-5）200
- NewAPI 渠道测试：`GET /api/channel/test/76?model=claude-opus-5` → success，4.75s

## 备注

- 创建走 fork 标准路径：`POST /api/channel/` body `{"mode":"single","channel":{...}}`（与 tabitoken 文档同款 fork API 约束）
- 渠道仅入 opus 池；gpt-5.6-sol 系列如日后上游修复，需重新直连验证后再补 models
- 权重策略：新源小权重（5），如稳定性验证通过可再调；改纯备份则 weight 置 0

## 回滚

- 渠道：`POST /api/channel/76/status {"status":2}`（与 guardian.py `disable_channel` 同款）
