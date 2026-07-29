# gorouter.app Claude 渠道接入 NewAPI（failover）

日期：2026-07-29
渠道：`id=26` `gorouter-claude` + `id=27` `gorouter-claude-2`（同网关双 key，NewAPI v1.0-rc.21，VPS 47.112.162.80）

## 背景

`https://gorouter.app` 提供一个基于 Kiro / CodeWhisper 账号池的 Claude 网关。
key `sk-cdX...akjh`（脱敏，完整值见 NewAPI 渠道配置）。

## 连通性实测

- `GET /v1/models` → 200，返回 6 个模型：
  `claude-opus-4-8` / `claude-opus-4-8-thinking` / `claude-opus-5` /
  `claude-opus-5-thinking` / `claude-sonnet-5` / `claude-sonnet-5-thinking`，
  全部 `supported_endpoint_types: ["anthropic","openai"]`。
- `POST /v1/chat/completions`（openai 格式，claude-opus-5）→ 200 PONG。
- `POST /v1/messages`（anthropic 原生，claude-opus-5）→ 200 PONG，
  usage 带 `kiro_credits` 计费字段。
- 两种协议均通，无客户端锁定。

## 接入决策

- **接三主力**：`claude-opus-5`、`claude-sonnet-5`、`claude-opus-4-8`
  （thinking 变体不单独接，由 Kimi 的 thinking 开关控制）。
- **并入现有 zg-newapi claude 池做 failover**：priority `45`，
  排在主力池之下（id 18 = 55，id 3 = 50）。

## 落地方式

本版本 NewAPI 管理面 `POST /api/channel` 会 Go panic，因此走 **SQLite 直插**：

- 备份：`/opt/new-api/data/backups/one-api.before-gorouter-20260729-181809.db`
- `channels` 表插 `id=26`：`type=14`（anthropic），
  `base_url=https://gorouter.app`（anthropic 类型**不带 `/v1`**），
  `models=claude-opus-5,claude-sonnet-5,claude-opus-4-8`，
  `group=default`，`priority=45`，`status=1`，
  `channel_info` 照抄 ch18 的 BLOB（`is_multi_key=false`）。
- `abilities` 表同步三行：三个模型 × `channel_id=26`，
  `group=default`，`priority=45`，`weight=10`，`enabled=1`。
- `podman restart new-api`，`/api/status` → 200，渠道 26 活跃。
- **`auto_ban=0`**（2026-07-29 code review 后调整，备份
  `backups/one-api.before-autoban-20260729-184113.db`）：sonnet-5 只有渠道 26
  一个后端，若保留默认 `auto_ban=1`，gorouter 偶发 5xx/限流会被 NewAPI 自动禁用
  → sonnet-5 直接不可用且需手动重启。关掉自动禁用，靠 priority 兜底。
- **第二 key 渠道 `id=27` `gorouter-claude-2`**（2026-07-29 追加）：克隆渠道 26
  全部字段（`channel_info` BLOB 逐字节一致），仅换 key，同 `priority=45` /
  `auto_ban=0`，同三模型。两渠道同优先级 → NewAPI 在池内按 key 轮询，对冲
  单 key 额度耗尽/被封。abilities 同步三行。
  - 连通性：`gorouter.app` Cloudflare 对**缺 User-Agent** 的请求返回 403
    (error 1010)，实测请求须带浏览器 UA；带 UA 后新旧 key 均 200 出 sonnet-5。

## Kimi CLI 配置

现有别名 `zg-newapi/claude-opus-5` / `claude-opus-4-8` / `claude-sonnet-5`
已全部走 `zg-newapi-anthropic` provider（网关 `https://aliyun.donglicao.com`）
→ NewAPI 池，无需新增别名。gorouter 作为 priority 45 渠道自动并入。

`kimi doctor config` → All valid。

## 端到端验证

- 现有 claude 渠道（3/9/18）仅服务 opus 系列，**无一服务 sonnet-5**，
  故 `claude-sonnet-5` 请求必然路由到渠道 26（gorouter）。
- `kimi -p -m zg-newapi/claude-sonnet-5 "reply PONG"` → **PONG**。
- 证明整条链路打通：Kimi → 网关 → NewAPI 池 → gorouter → Kiro 账号池。

## 注意

- `kiro_credits` 说明上游是 Kiro 账号池，可能有并发/额度限制，未实测到限流。
- 若 gorouter 挂了，opus 请求自动回落主力池（priority 55/50）；
  sonnet-5 现有渠道 26/27 两个同网关 key 兜底，单 key 被封/耗尽仍可轮询到另一个；
  但两者共用 gorouter.app 上游，网关整体不可用时 sonnet-5 仍会全断。
- 渠道 26/27 均 `auto_ban=0`，故 gorouter 报错不会被自动禁用；代价是要靠监控/手动
  发现它持续失败。想彻底消除 sonnet-5 单上游，需补一个非 gorouter 的 sonnet-5 来源。
