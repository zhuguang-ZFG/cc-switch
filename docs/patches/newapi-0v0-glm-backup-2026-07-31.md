# NewAPI 接入 0v0.club glm-5.2 备份渠道（2026-07-31）

将 0v0.club 作为 glm-5.2 的第四源接入 NewAPI，权重较低作备份，与现有 wintoken/tokenrhythm 共同分担。

## 1. 上游探测

`https://0v0.club/v1/models` 列出多个模型，其中 `glm-5.2` 可调用。直连实测：

```text
POST /v1/chat/completions model=glm-5.2 max_tokens=256 -> 200
content: OK
usage.completion_tokens: 92（其中 reasoning_tokens: 90）
```

注意：该上游 `glm-5.2` 的 reasoning 占用较多 completion 预算，`max_tokens` 给得较小时会出现 http200 但 `content` 为空（reasoning 占满）。客户端调用时应给足 `max_tokens`（建议 ≥512）。流式返回内容为空，当前按非流式使用。

## 2. 接入配置

新增渠道 ch40：

| 字段 | 值 |
|---|---|
| id | 40 |
| name | 0v0-glm |
| type | 1（OpenAI） |
| base_url | `https://0v0.club` |
| models | `glm-5.2` |
| status | 1 |
| weight | 5 |
| group | default |
| auto_ban | 1 |
| key | 2 个 key 换行分隔 |

abilities 表同步启用 `channel_id=40, model=glm-5.2, enabled=1`。

当前 glm-5.2 渠道权重分布：

| 渠道 | 权重 | 说明 |
|---|---|---|
| ch14 wintoken-glm | 10 | 主源，稳定 |
| ch37 tokenrhythm-glm-1 | 10 | 主源 |
| ch38 tokenrhythm-glm-2 | 10 | 主源 |
| ch40 0v0-glm | 5 | 备份源 |

## 3. 验证

- **单独验证**：临时禁用 ch14/37/38，只留 ch40，网关调 glm-5.2 3/3 http200，其中 2 次返回正常 content。
- **混跑验证**：恢复三主源后连打 12 次，全部 http200；ch40 因权重 5/35≈14%，短期未随机命中属正常。

## 4. 注意

- 0v0.club 的 `glm-5.2` 流式内容为空，当前客户端默认走非流式不受影响；若后续开启流式需单独评估。
- channels.key 字段写入 2 个 0v0.club key（换行分隔），NewAPI 会自动轮询，单 key 限流/封禁时可 fallback。
- 多 key 验证：追加第二个 key 后连打 5 次，全部 http200 且有 content。
- 若 0v0.club 稳定性好，后续可考虑把权重提到 10 与主源平权。

> 安全：本文档不含完整 API key、VPS 密码。
