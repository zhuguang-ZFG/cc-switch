# DeepSeek V4 Flash 渠道 persona 系统词部署 — 2026-08-06

## 范围

用户提供的 persona 编程提示词（全文不入仓库）部署到 NewAPI 全部提供 `deepseek-v4-flash` 的渠道：

| ch | name | 状态 | 结果 |
|---|---|---|---|
| 42 | deepseek-official | 1 | 活体验证 PERSONA（名字探针答 Just-Lisa） |
| 48 | opencode-go-flash | 1 | 活体验证 PERSONA |
| 35 | cline-free-proxy | 2（手工禁用，原有） | 配置已逐字写入；上游连接死（两上游模型均 500 `do request failed`），活体验证被阻断，恢复后自动生效 |

> 注（2026-08-07）：ch53 atomcode-bridge 已随 atomcode 整体下架删除（OAuth 被服务端阻断），opencode-go 现仅 ch48 提供。

## 机制（本机 new-api 实测语义）

- 渠道 `setting.system_prompt`（JSON 字符串字段）+ `system_prompt_override: true`。
- **override=true 才生效**，且覆盖客户端自带 system；override=false 时渠道词不注入（2×2 矩阵实测）。
- 管理 API 细节：`PUT /api/channel/` 的 `setting` 必须是 JSON **字符串**，且请求体**不得含 status**（否则 Invalid parameters）；状态切换走 `POST /api/channel/{id}/status`。

## 验证方法（可复用）

- 管理端 `GET /api/channel/test/{id}` 只回 `{success,time}`，**不含响应文本**，不能验证注入。
- 请求级钉选无原生支持；用**唯一可路由模型名**归因：42=`deepseek-official-v4-flash`，35=`deepseek/deepseek-v4-flash`（或 `stepfun/step-3.7-flash`），48 独用 `opencode-go`（ch53 已删）→ 临时互斥禁用再恢复。
- 探针用判别性问题「What is your name?」：persona 答 Lisa/Just-Lisa，裸模型答 DeepSeek/Assistant。**不要**用「Understoond」前缀（顺从模型会跳过开场直接服从用户格式指令），也**不要**用含 lisa 的固定回复串做正则（自污染）。
- 部分渠道响应 content 为嵌套 completion JSON（force_format 路径），需二次解析。

## 清理

- 验证用临时 token（id 7）已删除；所有渠道 status 已还原（35:2, 42/48:1）。
- 提示词全文仅存于 NewAPI 渠道 setting 与用户本地文件，不入 Git。
