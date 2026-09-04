# qwen3-8-27b 渠道故障路由复盘（2026-09-04 晚）

## 现象

用户报告：qwen3-8-27b "总是故障路由"（OMP smol/commit 角色主力模型）。

## 取证结论

模型与路由逻辑本身健康；故障在渠道侧，三只渠道两只残废：

1. **ch88 runinfra 余额耗尽**（根因）：21:54:08 GIN 实锤
   `channel error (channel #88, status code: 402): Out of credits: balance $0.0200, required $0.0300`。
   白天它作为 p49 首选，被选中即 402 → failover 到 ch124 → groq 拒
   `chat_template_kwargs`（thinking 请求恒 400，16:28/17:46/18:36 三连）→ 整条 relay 失败
   → OMP 冷却 → smol/commit 全落 deepseek-v4-flash fallback = "总是故障路由"。
2. **ch124 groq**：活着但只服务非 thinking 请求；smol/commit 恒带 effort → 到它必 400。p40 兜底位保留。
3. **ch113 bai**：早已禁用（余额门槛 400），恢复探针今天 27 次（19:48 起 5-8 分钟一波×3），
   每次失败只记 Guardian WARNING——纯噪音，等 b.ai 充值后按 runbook 三条件恢复。
4. **ch112 yjs**：全天健康（10 次渠道测试全过），禁用 ch88 后成为唯一服务渠道。

附注：DB 侧 type=5 错误行自 08-01 断流（独立缺陷，见对话记录），本复盘全部证据来自
oneapi GIN 日志 + DB type=2 行 + 直接探针，DB 错误表完全不可用。

## 处置

- 快照：`~/.new-api-local/backups/new-api-before-ch88-disable-20260904-215608.db`（155,959,296 B）
- 禁用 ch88：直接 DB 写（API `PUT /api/channel/` 对本 build 全量/最小体均返回
  `Invalid parameters`，待查；ch75 同款模式）：
  `UPDATE channels SET status=2 WHERE id=88;`
  `UPDATE abilities SET enabled=0 WHERE channel_id=88 AND model='qwen3-8-27b';`
- 烟测：qwen3-8-27b + reasoning_effort=high → **200 / 4.5s，全走 ch112**，402 往返消失。
- 验证时注意：从 models.yml 抓 apiKey 必须 `tr -d '\r\n'`——CRLF 的 `\r` 混进
  Authorization 头会让 Go 标准库直接回 text/plain "400 Bad Request"（假故障，别被它骗）。

## 恢复路径

- runinfra 充值后：`UPDATE channels SET status=1 WHERE id=88;`
  `UPDATE abilities SET enabled=1 WHERE channel_id=88 AND model='qwen3-8-27b';`（或恢复后 API PUT）
- 单点风险：qwen3-8-27b 现仅 ch112 yjs 一只；yjs 挂则 smol/commit 全走 fallback
  deepseek-v4-flash（可接受，但失去 27B 档位的低成本小任务承载）。
