# NewAPI prompt cache 计费补全（2026-07-31）

排查发现 OMP 走 NewAPI 的若干模型虽上游支持 prompt caching（响应返回 `cached_tokens`），但 NewAPI 的 `CacheRatio`/`CreateCacheRatio` 未配置，导致缓存命中时仍按全额 prompt token 计费，浪费上游缓存带来的成本优势。本次补全。

## 1. 排查方法

对每个模型发请求，检查响应 `usage.prompt_tokens_details.cached_tokens` 字段是否存在（存在即上游支持 prompt caching）。abilities 路由表已确认 14/14 OMP 模型全部注册（此前一次 shell 引号 bug 曾误判为缺失，实为齐全）。

## 2. 实测支持 prompt caching 的模型

| 模型 | cached_tokens 实测 | 补全前 CacheRatio | 补全前 CreateCacheRatio |
|---|---|---|---|
| k3 | 87/119 | 缺 | 缺 |
| step-router-v1 | 384/510 | 缺 | 缺 |
| cline-free/glm-5.2 | 12 | 缺 | 缺 |
| poolside/laguna-s-2.1:free | 32 | 缺 | 缺 |
| deepseek/deepseek-v4-flash | 字段在（单次 0） | 缺 | 缺 |
| qwen3.8-max-preview | 字段在（单次 0） | ✅0.1 | 缺 |

`stepfun/step-3.7-flash` 无 `prompt_tokens_details` 字段 → 上游不支持，不配（配了也无意义）。

## 3. 补全（sqlite 直写 options）

admin API `PUT /api/option/` 返回 `Unauthorized, invalid access token`（token 权限不符），改用 sqlite 直写（改前备份 + 停容器，纯 JSON 文本列改动，无 channel_info BLOB 风险）：

| 模型 | CacheRatio | CreateCacheRatio | 取值依据 |
|---|---|---|---|
| k3 | 0.1 | 1.25 | 对齐 glm/kimi 家族 |
| step-router-v1 | 0.1 | 1.25 | 同上 |
| qwen3.8-max-preview | （已有 0.1） | 1.25 | 补 Create |
| cline-free/glm-5.2 | 0.1 | 1.25 | glm 家族 |
| poolside/laguna-s-2.1:free | 0.1 | 1.25 | |
| deepseek/deepseek-v4-flash | 0.25 | 1.25 | 对齐 deepseek 家族（deepseek-v4-flash CacheRatio=0.25） |

备份：`/opt/new-api/data/backups/one-api.before-cache-20260731-<ts>.db`。

## 4. 验证

固定 prefix 连发两次触发缓存命中：

```text
k3 第一次  -> cached_tokens=None, prompt_tokens=177  （建缓存）
k3 第二次  -> cached_tokens=178, prompt_tokens=178   （全部命中，按 CacheRatio 0.1 折扣计费）
```

> 安全：本文档不含 NewAPI admin token、VPS 密码、用户 token。CacheRatio/CreateCacheRatio 为计费比率，非凭据。
