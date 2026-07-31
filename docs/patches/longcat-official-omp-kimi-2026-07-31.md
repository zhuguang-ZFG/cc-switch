# LongCat 官方直连接入 OMP + Kimi（2026-07-31）

美团 LongCat 官方 API 直连（`api.longcat.chat`）接入 OMP 与 Kimi Code CLI。按用户选择：**只接本地客户端，不动 NewAPI**。

## 1. 端点发现（关键）

- **`longcat.chat` 主站是网页网关**（`/v1/models`、`/v1/chat/completions` 都返回 HTML 或美团 APIGW 50001 错误），不是 API。
- 官方 API 基址是 **`https://api.longcat.chat`**（CC Switch 预设同源）：
  - OpenAI 兼容：`https://api.longcat.chat/openai/v1`（`/chat/completions` 与原生 `/responses` 均实测 200）
  - Claude 协议：`https://api.longcat.chat/anthropic`（Claude Code 预设用，本次未用）
- 模型：`LongCat-2.0`，官方 `/models` 返回 1M context / 131072 max output，text-only。

## 2. 协议行为实测

- `chat/completions` 默认**带 reasoning**：响应只含 `reasoning_content`，`max_tokens` 会被思考吃光（16 tokens 全被吃，`finish_reason=length` 无 content）。
- `reasoning_effort: minimal` 时正常出 `content`（`LONGCAT-OK`，`finish=stop`）。
- 原生 `/responses` 端点 200，输出含 `reasoning` 块（标准 Responses 协议）。
- usage 字段带 LongCat 特有缓存计数（`cache_read_tokens`/`cache_write_tokens`/`effectiveCachedTokens`），标准 `prompt_tokens_details.cached_tokens` 亦存在。

## 3. 客户端接入

### OMP（`~/.omp/agent/models.yml`）

新增 `longcat` provider（`openai-completions`，`api.longcat.chat/openai/v1`），模型 `LongCat-2.0`：1M ctx / 131072 out / `reasoning: true` / `input: [text]`。

### Kimi（`~/.kimi-code/config.toml`）

- `[providers.longcat]`（type=openai，官方端点 + key）
- `[models."longcat/LongCat-2.0"]`（1M ctx / 131072 out / `capabilities = []` / display "LongCat 2.0 (官方)"；保守不配 thinking，`reasoning_content` 兼容待真实 agent 会话验证）

## 4. 验证

```text
GET  https://api.longcat.chat/openai/v1/models          -> LongCat-2.0 (1M/131K)
POST /openai/v1/chat/completions (reasoning_effort=minimal) -> LONGCAT-OK
POST /openai/v1/responses                               -> 200, reasoning 块正常
omp models longcat                                      -> LongCat-2.0 1M/131K, thinking minimal..xhigh
kimi doctor config <config.toml>                        -> OK
omp -p --model longcat/LongCat-2.0                      -> OMP-LONGCAT-OK
kimi -m longcat/LongCat-2.0 -p                          -> KIMI-LONGCAT-OK
```

## 5. 与 NewAPI 的关系

- VPS NewAPI 当前**无 LongCat 渠道**（历史 `#90` 已在极简重建时消失）。
- Claude Code 的 Haiku 角色 `LongCat-2.0` 现仍走 ch122 Agnes 中转（`LongCat-2.0` → `agnes-2.0-flash` 映射），**本次未改动**；本机 OMP/Kimi 走的是官方直连独立来源。
- 官方 key 未写入仓库；文档使用脱敏缩写 `ak_2UO…d9L`。若后续要 Claude Haiku 走官方直连，需在 NewAPI 建渠道（参照 ch42 deepseek 官方模式），另行决策。

## 6. 注意

- LongCat-2.0 是 **text-only**：图片会按 media-scrub 白名单替换为不支持标记（CC Switch v3.17.0 行为），OMP 侧 `input: [text]` 已声明。
- 官方 `/models` 只列 `LongCat-2.0` 单模型；文档化程度最低的 `/responses` 工具契约建议真机冒烟后再用于 Codex 类工具调用场景。
- key 为平台 API key（`longcat.chat/platform/api_keys` 生成），仅存本机配置文件。
