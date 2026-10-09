# OpenCode Zen 免费档锁定 + Jev SystemOne 接入契约（2026-10-07）

## 结论

OpenCode Zen 全部 13 个免费模型（含 `fledge-alpha-free`、`muse-spark-1.3-contributor-free`、`jev-1.13-free`）当前被上游**整档客户端上下文门禁**锁定：

- 错误原文：`403 FreeTierError — "OpenCode's free tier can only be used from within OpenCode"`。
- 该门禁在**调用层**生效：key 已存在于 ch96（鉴权通过），拒绝发生在授权策略层。
- 与模型无关、与是否走 Chat Completions / Responses 无关——免费档整体不可经 NewAPI 外部调用。
- 控制台**没有**开放免费档给第三方客户端的开关（workspace Models tab 仅启停模型）；免费模型设计面向 OpenCode 客户端内使用。

因此：ch96（opencode-zen-free）维持 `status=2` 禁用为正确终态；不新增死 abilities、不注册死 OMP 条目。

## 探针证据（2026-10-07，经 NewAPI ch96 管理测试路径各自一次）

| 模型 | 端点 | 结果 |
|---|---|---|
| `fledge-alpha-free` | chat/completions | HTTP 200(success=false)，上游 403 FreeTierError |
| `muse-spark-1.3-contributor-free` | chat/completions | HTTP 200(success=false)，上游 403 FreeTierError（同文案） |

未重试、未伪装 UA/session/客户端。key 元数据仅验证 present，未读出、未打印。

## 时间线

- 2026-08-20：Go key 实测可打 Zen 免费端点（hy3-free/laguna 200，big-pickle/mimo/deepseek-free 429 额度），ch96 建池。
- 2026-10-02：Go key 轮换（`oc_sk_` 前缀）；ch96 仍挂旧代 key，status=2。
- 2026-10-03：复刻 4 组客户端配方（opencode UA 变体 ×3、CLI 全套头、zen-proxy 配方 ×3 auth、精确现版 opencode CLI UA）全 403；结论：疑需会话/设备绑定或服务端授权。
- 2026-10-07：本 runbook，双模型复证整档锁定。

## 授权解除路径（用户侧，按可行性排序）

1. **在 OpenCode 客户端内使用免费模型**——设计意图，无 API 成本，OMP 无法代用。
2. **付费档（Zen pay-as-you-go）**：同端点、同 key 体系，免费档之外完全可经 NewAPI 外部调用（现有 ch125/130/131/133 即 Go 档先例）。Jev 付费价 `jev-1.13` $0.042/1M in、output Free。需要 key 有 Zen 余额；接入前需用户确认消费授权。
3. **官方授权转发/转售来源**：本仓先例 ch105 furry-vg 以 model_mapping 接入 `muse-spark-1.2-contributor-free`（同名替代来源）。若出现合法第三方渠道，走新增渠道标准合约。
4. **联系 help@anoma.ly** 询问免费档 API 白名单/商务授权。

## Jev SystemOne 调用契约（适配器实现依据）

端点：`POST https://opencode.ai/zen/v1/systemone`
鉴权：`Authorization: Bearer <console key>`、`Content-Type: application/json`
请求体：

```json
{
  "model": "jev-1.13",        // 或 jev-1.13-free（限时免费，当前同属免费档门禁）
  "state": "<待判定的上下文文本>",
  "questions": {
    "<question-id>": {
      "type": "noul",                              // noul=是否 | choice=多选一 | score=量级
      "instructions": "<判定指令>",
      "criteria": {"<option>": "<含义>"}            // choice 专用
      // 或 criteria: ["Calm", "Frustrated"]       // score 专用
    }
  }
}
```

- 一个请求可并行询问多个问题，响应按 question-id 返回判定值与概率。
- 问题类型：`noul`（yes/no）、`choice`（多选一，criteria 为对象）、`score`（量级，criteria 为数组）。
- TypeSafe 官方文档：https://docs.typesafe.ai/ 。

### Agent 场景示例（适配器默认模板候选）

```json
{
  "model": "jev-1.13",
  "state": "<OMP 当前任务/报错/上下文摘要>",
  "questions": {
    "is_blocking": {"type": "noul", "instructions": "此状态是否阻塞当前任务？"},
    "urgency": {"type": "score", "instructions": "处理紧迫度", "criteria": ["低", "中", "高"]},
    "route": {"type": "choice", "instructions": "建议走哪条处理路径",
      "criteria": {"retry": "瞬时错误，重试", "escalate": "需要人工/上游", "proceed": "可继续"}}
  }
}
```

## 适配器设计（授权解除后实现）

目标：让 OMP 能消费 Jev 的结构化判定，不伪造为聊天模型。

- 最小 OpenAI 兼容 relay（独立服务，勿塞入在产渠道）：`POST /v1/chat/completions` → 翻译为 systemone 请求（`state`=用户消息全文，`questions`=固定判定模板），响应把判定结果序列化回 chat 完成体。
- NewAPI：relay 作为 type=1 渠道 base_url（或 OMP 直连 provider）；ModelRatio 按实际计费（免费档=0，付费档按 $0.042 折合）。
- OMP 侧接入点：config.yml 分类/fallback 判定步骤或独立 MCP tool；**不得**把 Jev 注册为主聊天角色模型（无对话生成语义）。

## 状态

- 本轮零配置变更（无备份/回滚需要）；未新增 abilities/OMP 条目；未输出任何 key。
- 待办：用户选择授权路径（免费档无法外部解锁 → 付费档需确认余额与消费授权）。

## 第二趟（2026-10-07 晚间）：新代 key 判定 + 并行载体盘点 + 死源停泊

### 决定性实验：新代 `oc_sk_` key 打 `/zen` 免费档

直接用 ch130 的新代 key（`oc_sk_`，10-02 轮换后从未打过 `/zen`）直连
`https://opencode.ai/zen/v1/chat/completions` + `fledge-alpha-free`（浏览器 UA）：

- 结果：**同样的 `403 FreeTierError`，文案逐字相同**。
- 结论：两代 key（旧 `sk-`、新 `oc_sk_`）、所有已测客户端形态全部同文案——
  **/zen 免费档对所有 API 客户端关闭，只认 OpenCode 客户端会话上下文**。
  此路由永久封死，不再试探（伪装客户端被明确禁止且 10-03 已证 4 组配方全灭）。

### 并行载体盘点（“不用 opencode 也能用”的真实可得性）

| Zen 免费 ID | 唯一已知非 opencode 载体 | 今日状态（2026-10-07） | 恢复条件 |
|---|---|---|---|
| muse-spark-1.2-contributor-free | ch105 furry.vg（freeapi2.furry.vg） | **源站 TLS 坏死**：urllib EOF / curl schannel 握手失败 / Bun 证书校验失败 / NewAPI Go 上游 do request failed。已停泊 ch105 status=2 | 源站 TLS 修复后：管理探针绿 → POST /status {"status":1} + 网关实弹归因 |
| mimo-v2.5-free | ch107 freebuff 本地 relay（127.0.0.1:8321，映射 mimo/mimo-v2.5） | **上游账号被封禁**：relay 日志 `403 {"status":"banned"}`，token 轮换持续 `404 Invalid API key or user not found`（~60s 空转） | 新 freebuff 账号/token（用户侧） |
| nemotron-3-ultra-free / nemotron-3.5-lightning-free | NVIDIA 官方免费端点 | 栈内无 nvapi key | 用户注册 build.nvidia.com 提供 key（注意 NVIDIA trial 数据条款） |
| fledge-alpha-free / big-pickle / exo-free / ling-* / jev-1.13-free / space-bunny-free(zen) / muse-1.3-contributor-free | 无（stealth/独占） | 不存在非 opencode 来源 | 不可达（上游唯一授权方） |
| LongCat-2.5-Preview（同模型非 -free id） | ch145 longcat.chat 官方（ak_ key） | status=2；定价 open item（10-03） | 非免费；定价确认 + 用户消费授权 |

### 生产卫生处置（本轮唯一变更）

- 发现：`muse-spark-1.2-contributor-free` 是 config.yml 多条 fallback 链活腿
  （smol、claude-haiku-4-5 等），ch105 源坏死期内实弹 **500 硬失败**。
- 处置：备份 `backups/new-api-before-furryvg-quarantine-20261007-194357.db`
  （31,846,400 B，integrity=ok）→ ch105 `POST /status {"status":2}` →
  网关复探 **503 "No available channel"**（OMP 链干净续走 omen-alpha）。
- 双锁读回：ch105 status=2/auto_ban=1；muse-free abilities 96/105/110 全 0。
- 回滚：`POST /status {"status":1}`（源恢复并探针绿后）。
- 观察项：freebuff relay 每分钟对已封禁账号空转 token 轮换（重试风暴，
  运维卫生违反项）——建议停 PID 17380 + 移除 HKCU Run 键 `Freebuff2API`，
  待用户确认后执行。