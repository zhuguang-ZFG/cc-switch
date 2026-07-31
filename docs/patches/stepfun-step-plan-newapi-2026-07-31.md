# Step Plan step-router-v1 接入 NewAPI（ch36）（2026-07-31）

把阶跃星辰 Step Plan 通道的智能路由模型 `step-router-v1` 接入 NewAPI（渠道 36），让所有走 NewAPI 的客户端可用。该模型是触达 `deepseek-v4-pro` 的唯一途径。

## 1. 关键发现：deepseek-v4-pro 不可直连

用户目标是稳定调用 Step Plan 的 `deepseek-v4-pro`。实测该通道：

- `GET /v1/models` 列出的可调用模型：`step-3.7-flash`、`step-router-v1`、`stepaudio-*`、`step-image-edit-2`、`step-3.5-flash*`。**没有 `deepseek-v4-pro`**。
- 直接调 `deepseek-v4-pro` 返回 `404 model_invalid`。
- `deepseek-v4-pro` 与 `step-3.7-flash` 是 `step-router-v1` 的**内部引擎**，路由器按请求特征（消息轮数、输入量、工具数量）自动调度，无法在请求中强制指定，响应也不暴露实际命中的引擎。

结论：要经此通道用 deepseek-v4-pro，只能调 `step-router-v1`（复杂请求自动路由到 deepseek-v4-pro，简单请求走 step-3.7-flash）。用户确认接入 `step-router-v1`。

## 2. 端点与协议

- Base URL：`https://api.stepfun.com/step_plan/v1`（仅 Step Plan 通道可用）。
- OpenAI 兼容 `chat/completions`；响应带 `reasoning`/`reasoning_content`（思维链）。
- 上游稳定：直连 5/5、经 NewAPI 5/5、流式 OK。

## 3. NewAPI 渠道 ch36（sqlite 直写）

`POST /api/channel` 在本版本触发 Go nil-pointer panic，沿用 sqlite 直写（先 `podman stop new-api`，`channel_info` 必须 BLOB）：

| 字段 | 值 |
|---|---|
| id | 36 |
| type | 1（OpenAI） |
| name | stepfun-step-plan |
| base_url | `https://api.stepfun.com/step_plan`（**不带 `/v1`**，NewAPI 自动补 `/v1/chat/completions`；与 ch31 aliyun-qwen38 的 `/compatible-mode` 约定一致） |
| models | `step-router-v1` |
| group / priority / weight | default / 50 / 10 |
| channel_info | 单 key BLOB（`typeof=blob` 已验证） |

abilities：`('default','step-router-v1',36, enabled=1, priority=50, weight=10)`。

定价（`SelfUseModeEnabled=false`，必须配否则 503）：`ModelRatio["step-router-v1"]=0.5`、`CompletionRatio["step-router-v1"]=2.0`，对齐它能路由到的 `DeepSeek-V4-Pro` 引擎；如需改计费比例，调这两个 option 即可。

改前备份：`/opt/new-api/data/backups/one-api.before-stepfun-20260731-025025.db`。

## 4. 验证（NewAPI 公网入口）

```text
非流式 step-router-v1（经 aliyun.donglicao.com）  -> 5/5 finish=stop
流式 step-router-v1                              -> STREAM-OK
响应含 reasoning_content                          -> 是
```

## 5. 使用与注意

- 任何走 NewAPI 的客户端把 `model` 填 `step-router-v1` 即可（OMP 经 `zg-newapi` provider 直接用裸名；Kimi 需加 `[models."zg-newapi/step-router-v1"]` 别名）。

**落地状态（2026-07-31 补配）**：两端客户端均已配置并冒烟验证——

- OMP `~/.omp/agent/models.yml` `zg-newapi` provider 增加 `step-router-v1`（1M ctx / 128K out / `reasoning: true`）。
- Kimi `~/.kimi-code/config.toml` 增加 `[models."zg-newapi/step-router-v1"]`（1M ctx / 128K out / `capabilities = ["thinking"]`）。
- 验证：`kimi -m zg-newapi/step-router-v1 -p` → `KIMI-STEP-OK`；`omp -p --model zg-newapi/step-router-v1` → 返回正常文本（未严格复述短指令，属 step-router-v1 内部引擎路由差异，非配置问题）。
- 注意区分：`stepfun/step-3.7-flash`（Cline 池）与 `step-router-v1`（ch36 Step Plan 通道）是两个独立来源。
- 无法保证每个请求都命中 deepseek-v4-pro——路由由上游按请求复杂度自动判定。需要稳定 deepseek-v4-pro 语义时，构造"复杂"请求（多工具/长上下文）可提高命中率，但不构成保证。
- Step Plan 为付费订阅通道，消耗用户自己的阶跃额度。

> 安全：本文档不含 Step Plan API key、NewAPI admin token、VPS 密码。
