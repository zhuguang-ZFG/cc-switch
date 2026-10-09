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

## 2026-10-03 增补：tiny 角色 + Groq 快腿 + intern 池四腿

- `tiny` role 切 `zg-newapi/qwen3-8-27b`（备份 `~/.omp/agent/config.yml.bak-20261003-tiny-qwen`；重启 OMP 生效）。
- **ch124 Groq prio 40→50 升主腿**（389ms 快腿先行），ch88 runinfra 49 降为备。分布实测 4/4 落 ch124（252–936ms，`logs.channel_id` 归因）。快照 `new-api-before-groq-prio-20261003-161532.db`。
- thinking 形状实测：`reasoning_effort=high` → Groq 200；@512 预算 `content='pong'` finish=stop（reasoning_tokens=17，落 `reasoning` 字段；早前 `content=''` 系 16-token 探针预算耗尽，非 Groq 缺陷）。`chat_template_kwargs` → **400 且 NewAPI 不 failover**（relay 终态，日志只见 ch124）——与 09-04 结论一致；translator（thinkingLevel:max）若走该形状由 OMP 层 fallback（omen-alpha）接住。
- **intern-discovery 池（ch140–143，4 个独立账户）加入兜底**：`model_mapping` `qwen3-8-27b→qwen3.8-27b`（上游带点命名），4×PUT + abilities 4 行（prio40 w1），ch140 映射 channel test success=True（676ms）。快照 `new-api-before-intern-qwen-20261003-161843.db`。
- 终态：`qwen3-8-27b` 六条 enabled 腿 = **Groq(50) > runinfra(49) > intern×4(40)**。
