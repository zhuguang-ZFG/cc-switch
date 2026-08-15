# claude-opus-5 渠道故障恢复预案（2026-08-15）——ch76+ch75 双渠道聚合，残余风险为双渠道同宕

## 风险陈述

`claude-opus-5`（Claude Code 主链路 + OMP slow 链首）default 组**活跃渠道 = ch76 sotamodel（weight=20）+ ch75 tabitoken（weight=8）**，均 status=1、enabled=1（2026-08-15 21:05 ch75 启用）。亲和 TTL=60s（同日从 600s 降低），过期后新会话在 ch76/ch75 按约 71%/29% 重新竞争，实现双渠道分流聚合。

`zg-claude-opus-5`（OMP 主链路实际模型名，亲和规则覆盖 `zg-claude-*`）同日 21:34 同步聚合到
ch75：ability（default/zg-claude-opus-5/ch75/enabled=1/pri50/w8）INSERT + ch75 `models` 追加别名；
ch75 `model_mapping` 的 `zg-claude-opus-5→claude-opus-5` 原本已存在，无需改动。变更前
`.backup` 快照 `new-api.db.bak-20260815-213358-zgopus5-ch75`（integrity ok），`/api/channel/fix`
热加载 36/36 成功，探针经网关 200（落 ch76，符合 w20:w8 概率）。

**单点风险仍存在**：ch76/ch75 同时宕机 → 无可用渠道；ch76 alone 宕机时 ch75 自动接管（keep_on_channel_disabled=false）。

既有缓冲：OMP slow 链 fallback `claude-opus-4-8 → k3 → deepseek-v4-pro`（模型降档不中断）；
Claude Code 直连流量无此缓冲，是本预案的主要保护对象。

## 检测信号

- guardian `MIN_ENABLED_CRITICAL_MODELS` 告警（opus-5 覆盖 0 渠道）；
- consume 日志出现 500 "无可用渠道"（model_name=claude-opus-5）；
- OMP slow 链实际落到 opus-4-8（降档信号，可通过 OMP 日志确认）。

## 止血决策树

1. **ch75（tabitoken）已常驻活跃**（2026-08-15 21:05 起 status=1，weight=8，日常承载约 29% 流量）；ch76 宕机时亲和自动释放，ch75 即接管——**通常无需手动操作**。
2. **ch57（gorouter）冷备**。ch75 也不可用时：启用前先探活 `POST /api/channel/test/57`，测试通过再执行下方启用步骤。
3. **ch72（anyrouter）仅在窗口开启时考虑**——窗口哨兵（`AnyRouter Window Canary`
   计划任务）的 Telegram 告警为准；429 池期勿动。
4. **零变更兜底**：不动 NewAPI，OMP 流量由 fallback 链吸收；Claude Code 用户
   临时切 agentrouter 直连（127.0.0.1:8788，模型 claude-opus-5，需代理 key）。

## 启用步骤（ch57 为例）

⚠️ fork 约束（2026-08-14 已复踩）：`PUT /api/channel` 对既有渠道报
`Invalid parameters`——**必须 DB 直改 + `/api/channel/fix`**：

```bash
sqlite3 ~/.new-api-local/new-api.db "update channels set status=1 where id=57"
# 然后让 fork 重建内存渠道缓存：
curl -X POST http://127.0.0.1:3002/api/channel/fix -H "Authorization: <admin token>" -H "New-Api-User: 1"
```

改 DB 前先备份（**禁热 `cp`**：WAL 模式下会丢 `-wal` 中未 checkpoint 的事务，快照撕裂且不自知）：
`sqlite3 ~/.new-api-local/new-api.db ".backup 'new-api.db.bak-<ts>-opus57'"`（已有同名惯例），
备后对新库跑 `PRAGMA integrity_check` 确认可用。

## 亲和行为与 TTL 配置

`channel_affinity_setting.keep_on_channel_disabled=false`：ch76 禁用/不可用时
亲和自动释放，ch75 立即接管；ch76 恢复 re-enable 后亲和重新钉住最先响应的渠道。

**TTL（2026-08-15 21:05 调整）**：`default_ttl_seconds` 600→**60**，`claude trace` 规则 TTL 同步降至 60s。
效果：每 60s 会话亲和过期，新会话按 weight 竞争（ch76:ch75 ≈ 20:8 ≈ 71%:29%），实现双渠道分流。
代价：prompt cache 热窗口缩短，60s 后新 session 首轮 cache miss 概率上升。
如 cache ratio 明显下降：回退 TTL，步骤见「回退」节。

亲和规则 `claude trace` 覆盖 `^(?:zg-agent-claude-.*|claude-.*|zg-claude-.*)$`（2026-08-15 21:04 修复），多渠道并存期无需手动调整规则。

## 验证

1. 日常验证（双渠道分流）：取 5 分钟窗口 consume 日志按 `channel_id` 计数，样本 ≥20 且
   `channel_id=75` 与 `76` 均出现、比例大致符合 weight 20:8（约 71%/29%）。
   **注意**：仅发 2 批请求不足为证——20:8 权重下两次采样同时覆盖两渠道的概率仅约 41%，
   样本不足会造成 ~59% 的假性失败误判。
2. ch76 故障模拟（**低流量窗口执行**；先 `POST /api/channel/test/75` 确认 ch75 健康、
   预置好恢复命令再动手）：DB `status=2 where id=76` + `/api/channel/fix`；
   等待亲和 TTL(60s) 过期后发请求；consume 日志 `channel_id=75`。恢复后 `status=1` + fix。
3. ch57 启用验证（仅 ch75 也宕时执行）：`POST /api/channel/test/57` 通过；发请求确认 `channel_id=57` 且 `channel_affinity.rule_name="claude trace"`。
4. 探针形状：urllib POST 3002，勿用 curl（3002/3003 对 curl 一律 text/plain 400，生产客户端不受影响）。

## 回退

**ch76 临时 disable 后恢复**：`update channels set status=1 where id=76` + `/api/channel/fix`；ch75 继续活跃，双渠道自动恢复并行。

**TTL 回退**（cache ratio 明显下降时）：首选恢复原值 **600**（原设初衷为覆盖 Anthropic 5 分钟
缓存窗，见 ops-stack-review-2026-08-11）；如需折中可用 300。
`update options set value='600' where key='channel_affinity_setting.default_ttl_seconds'`；
同步更新 `claude trace` 规则 `ttl_seconds=600`；`PUT /api/option/` 热加载两次（先 default_ttl，再 rules）。
