# NewAPI glm-5.2 prompt_cache_key 400 修复（2026-07-31）

glm-5.2 经 NewAPI 调用偶发 `400 未知请求字段：prompt_cache_key`。根因是 NewAPI 渠道亲和性的 `glm trace` 规则把 `prompt_cache_key` 透传/注入到上游，而新接入的 tokenrhythm 上游不认该非标准字段。

## 1. 现象与定位

错误日志（NewAPI logs 表）：

```text
status_code=400, 未知请求字段：prompt_cache_key   ch37/38 (tokenrhythm) token_name=cc-switch
```

只在 ch37/38（tokenrhythm）出现，ch14（wintoken）从不报错。

直连 tokenrhythm 上游对照确认：

```text
无 prompt_cache_key -> OK
带 prompt_cache_key -> 400 UNKNOWN_FIELD prompt_cache_key
```

## 2. 根因

NewAPI `channel_affinity_setting.rules` 的 `glm trace` 规则 `key_sources` 含 `{"type":"gjson","path":"prompt_cache_key"}`——它从请求体读 `prompt_cache_key` 作亲和 key，但**同时把该字段透传到上游**。wintoken 上游兼容该字段（不报错），tokenrhythm 上游严格、不认非标准字段就 400。这是接入 tokenrhythm（ch37/38）后引入的回归。

## 3. 修复

从 `glm trace` 规则 `key_sources` 移除 `prompt_cache_key`，只保留 `User-Agent`（亲和仍有效，此前 glm-5.2 粘滞验证靠的就是 UA fallback）。sqlite 直写 options（停容器写入，MEMORY_CACHE 重载）。改前备份 `one-api.before-glm-pck-fix-<ts>.db`。

```text
glm trace key_sources: [prompt_cache_key, User-Agent] -> [User-Agent]
```

## 4. 验证

```text
glm-5.2 连发 5 次 -> 5/5 成功（不再 prompt_cache_key 400）
最近渠道分布       -> ch14 OK / ch37 OK / ch38 OK（亲和仍粘滞，三源全健康）
```

## 5. 注意

- 其他规则（codex/grok/qwen/deepseek/longcat trace）的 `prompt_cache_key` key_source 暂未动——其对应上游目前都兼容该字段，未报错。若后续接入不兼容该字段的上游，需同样移除。
- `prompt_cache_key` 是 OpenAI 部分模型的非标准缓存提示字段，非所有上游都支持；NewAPI 亲和性用它作 key 时会透传，严格上游会拒。

> 安全：本文档不含 API key、VPS 密码。
