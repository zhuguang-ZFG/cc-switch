# muyuan.do 渠道接入（2026-08-14）

在本地 NewAPI（127.0.0.1:3002）新增两个渠道，上游 `https://muyuan.do`（NewAPI 系中转，🐾 计价 fork）。用户提供两把独立 key，同 URL。key 不落 repo/文档，仅存 NewAPI 渠道配置（同 seekai/sotamodel/dots 惯例）。

## Key 分族与处置

| Key | 上游可见模型 | 处置 |
|-----|--------------|------|
| key A | `gemini-2.5-pro`, `gemini-3.1-pro-preview` | **未接入**：鉴权有效但配额耗尽——两模型 chat 均 403 `pre_consume_token_quota_failed`（remain 🐾0.4，需 1.0/2.0）。按"不留死渠道"惯例暂缓，充值后再建 |
| key B | `gpt-5.5`, `gpt-5.5-openai-compact`, `zai-glm-5-2` | 接入 ch80/ch81（compact 除外，见下） |

## 渠道参数

| 项 | ch80 `muyuan-gpt55` | ch81 `muyuan-zai-glm` |
|----|--------------------|-----------------------|
| type | 1（OpenAI） | 1 |
| base_url | `https://muyuan.do`（根路径，NewAPI 自动拼 `/v1/...`；勿带 `/v1`） | 同左 |
| models | `gpt-5.5` | `zai-glm-5-2` |
| header_override | `{"User-Agent":"codex_cli_rs/0.42.0"}`（**必需**，见下） | 无 |
| priority / weight | 50 / 5（新源小权重先例） | 50 / 5 |
| status | 1 enabled，auto_ban=1 | 同左 |

abilities 表已随创建自动同步（两模型 enabled=1, p50/w5）。

## 上游特征（接入前直连实测）

- **UA 白名单（gpt-5.5 上游渠道）**：`curl/*`、`OpenAI/Python`、`Chrome/*`、`claude-cli/*`、`opencode/*`、`GeminiCLI/*`、`Go-http-client/*`、空 UA 全部 403 `channel:client_restricted`；仅 `codex_cli_rs/*` 放行。本地 NewAPI 出站为 Go UA，故 ch80 必须配 header_override（格式先例：ch24 `welfare-0xpsyche-responses`）。
- **zai-glm-5-2 无 UA 限制**：curl 与 Go UA 均 200。推理模型（`reasoning_content`），非流式首请求 TTFT ~26s，流式 ~2.2s。
- **gpt-5.5-openai-compact 是死模型**：`/v1/models` 列出但 chat/responses 均 404 `model_not_found`（"not supported by any configured account"）——未挂入任何渠道。
- gpt-5.5 端点：`/v1/chat/completions` 非流式 200（2.8s）、流式 SSE 200（2.5s）、`/v1/responses` 200（2.6s，status completed）。
- 错误族 `new_api_error` + paw 配额单位 → NewAPI 系中转。

## 验证

- NewAPI 渠道测试：ch80 `gpt-5.5` 2.2s ✅；ch81 `zai-glm-5-2` 6.8s ✅（ch80 通过即证明 header_override 生效——Go 默认 UA 直连必 403）。
- 网关 e2e（client-token 走 127.0.0.1:3002）：`gpt-5.5` 200 4.4s content="OK."；`zai-glm-5-2` 200 27.3s content="OK."（首请求冷启动，与直连 26s 一致）。
- 消费日志归因：gpt-5.5 → ch80、zai-glm-5-2 → ch81，无串渠。
- 存量 gpt-5.5 渠道均不竞争：ch2/ch70 status=3 weight=0、ch30 status=2；ch73（今日早些时候的消费日志来源）已删除。ch80 为当前唯一 enabled gpt-5.5 渠道。
- 网关 e2e（client-token 走 127.0.0.1:3002）：`gpt-5.5` 200 4.4s content="OK."；`zai-glm-5-2` 200 27.3s content="OK."（首请求冷启动，与直连 26s 一致）。
- 网关流式 e2e（stream:true 过 127.0.0.1:3002）：两者均 `text/event-stream` 200——`gpt-5.5` 3 chunks、首 delta 2.2s、finish=stop（证明 header_override 在流式路径同样生效）；`zai-glm-5-2` 5 chunks、首 delta 1.9s、finish=stop。

## OMP 注册

`~/.omp/agent/models.yml` 的 `zg-newapi` provider 新增两个 selector（先备份 `models.yml.20260814-231126-muyuan.bak`）：

- `zg-newapi/gpt-5.5`：reasoning，272k context / 128k output（pi.dev/models/openai/gpt-5-5 官方注册值；首版按 gpt-5.6-sol 姿态写 400k，已更正）。
- `zg-newapi/zai-glm-5-2`：reasoning，1M context / 131072 output（z.ai 官方文档与 OMP 官方注册表 pi.dev/models/zai/glm-5-2 一致；首版误写 128k/32k 保守档，已更正）。

OMP 实证探针（`omp -p --model`）：`OMP_GPT55_OK`（~31s 含启动）、`OMP_GLM52_OK`（~18s 含启动）均返回。OMP→本地 NewAPI 段 UA 不受上游限制影响，header_override 由 NewAPI 出站时套用。

## 备份与回滚

- DB 快照：`~/.new-api-local/backups/new-api-before-muyuan-channels-20260814-230308.db`（serialize 一致性快照，integrity_check=ok，64.6MB）。
- 回滚：`POST /api/channel/{80,81}/status {"status":2}` 禁用，或 `DELETE /api/channel/{80,81}` 移除；无需整库恢复。
- key A 后续接入：充值后直连验证 `gemini-2.5-pro` chat 200，再建 `muyuan-gemini`（同参数族，无 header_override 需求——Gemini 渠族 UA 限制未实测，建立前先探）。

## 备注

- 管理 API token 缓存已过期轮换一次（`AUTH_TOKEN_EXPIRED` → 重新 login 写回 `.admin-token-cache.json`）。
- `admin-credentials.json` / `client-token.json` 带 UTF-8 BOM，脚本读取需 strip（本次 bun 脚本已处理）。
- bun:sqlite 不支持 `VACUUM INTO`（syntax error）；DB 快照用 `Database.serialize()`。
