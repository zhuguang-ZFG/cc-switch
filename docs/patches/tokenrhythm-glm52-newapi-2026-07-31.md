# tokenrhythm GLM-5.2 接入 NewAPI（glm-5.2 去单点）（2026-07-31）

把 tokenrhythm.studio 的 GLM-5.2 接入 NewAPI（渠道 37/38），消除 glm-5.2 的单源单点故障。

## 1. 背景：glm-5.2 原是单源 SPOF

接入前 glm-5.2 在 NewAPI 只有 **ch14 wintoken-glm** 一个渠道（AGENTS.md 记的「wintoken+sensenova+vyceai 三源」实际只剩 wintoken）。glm-5.2 是日常主力模型，单源即单点故障。

## 2. 上游：tokenrhythm.studio

- 端点 `https://tokenrhythm.studio/v1`，VPS（大陆阿里云）**可达**，两个 key 均验证可用。
- 模型 `glm-5.2`（无问芯穹）：序列长度 1M、最大输出 128K、文本模态、不支持 Responses API。
- 上游定价：输入 ¥8/M、输出 ¥28/M、缓存命中 ¥2/M（NewAPI 的 glm-5.2 ModelRatio/CompletionRatio/CacheRatio 为全局每模型设置，已有 2.0/3.0/0.1，不因新增渠道改动）。
- 该端点还提供 glm-5/5.1、deepseek-v4-flash/pro、minimax-m2.5/2.7、kimi-k2.5/2.6 等 12 个模型（本次只接 glm-5.2）。
- **大陆优化后端 `https://a-ocnfniawgw.cn-shanghai.fcapp.run` 已废**：实测 403 `Current user is in debt`（账户欠费），不接。

## 3. NewAPI 渠道（sqlite 直写）

`POST /api/channel` 在本版本 panic，沿用 sqlite 直写（停容器 + channel_info BLOB）。两个 key 拆成两个单 key 渠道（避免 multi-key BLOB 复杂度，与 grok ch17/29 多源模式一致）：

| 渠道 | 名称 | base_url | 模型 | group/pri/weight |
|---|---|---|---|---|
| 37 | tokenrhythm-glm-1 | `https://tokenrhythm.studio` | glm-5.2 | default/50/10 |
| 38 | tokenrhythm-glm-2 | `https://tokenrhythm.studio` | glm-5.2 | default/50/10 |

base_url 不带 `/v1`（NewAPI 自动补 `/v1/chat/completions`，与本实例其他渠道约定一致）。改前备份 `one-api.before-tokenrhythm-<ts>.db`。

接入后 glm-5.2 三源：ch14（w20）+ ch37（w10）+ ch38（w10），按权重约 50:25:25 分流，配合 glm trace 亲和（同会话粘滞单渠道利于缓存命中）。

## 4. 验证

```text
glm-5.2 经 NewAPI 连发 6 次        -> 6/6 HTTP 200
完整响应                            -> content=GLM-OK + reasoning_content（思维链正常）
最近 30 次渠道分布                  -> ch14 ok=25 / ch37 ok=4 / ch38 ok=1，全部 0 失败
```

> 注：glm-5.2 是思维模型，小 max_tokens（如 32）会被思维链占满导致 content 空，调用需给足 max_tokens（≥256）。

> 安全：本文档不含 tokenrhythm API key、VPS 密码。
