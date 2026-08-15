# claude-opus-5 单点（ch76 sotamodel）恢复预案（2026-08-15）

## 风险陈述

`claude-opus-5`（Claude Code 主链路 + OMP slow 链首）default 组**唯一可用渠道 = ch76 sotamodel**
（abilities 实测：ch3/9/18/57/72/75 全部 enabled=0）。亲和规则 `claude trace` 把会话钉在 ch76
（prompt caching 收益来源），ch76 宕机即全链路无可用渠道。

既有缓冲：OMP slow 链 fallback `claude-opus-4-8 → k3 → deepseek-v4-pro`（模型降档不中断）；
Claude Code 直连流量无此缓冲，是本预案的主要保护对象。

## 检测信号

- guardian `MIN_ENABLED_CRITICAL_MODELS` 告警（opus-5 覆盖 0 渠道）；
- consume 日志出现 500 "无可用渠道"（model_name=claude-opus-5）；
- OMP slow 链实际落到 opus-4-8（降档信号，可通过 OMP 日志确认）。

## 止血决策树

1. **首选 ch57（gorouter）**。启用前先探活：`POST /api/channel/test/57`
   （管理 API，admin token + New-Api-User 头）。测试不过 → 转 2。
2. **次选 ch75（tabitoken）**。2026-08-10 遗留"未全面评估"，启用后需观察首轮错误率。
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

改 DB 前先备份：`cp new-api.db new-api.db.bak-<ts>-opus57`（已有同名惯例）。

## 亲和行为（无需手工干预）

`channel_affinity_setting.keep_on_channel_disabled=false`：ch76 禁用/不可用时
亲和自动释放，新启用渠道立即接管；ch76 恢复并 re-enable 后亲和自动回钉。
**不要**为多渠道并存期调整亲和规则——`claude trace` 规则覆盖 `^(?:zg-agent-claude-.*|claude-.*|zg-claude-.*)$`（2026-08-15 21:04 修复后），ch57/ch76 并存时 claude-opus-5 请求均命中规则，亲和自动钉住首次响应渠道。

## 验证

1. `POST /api/channel/test/57` 通过；
2. 生产形状探针（urllib，勿用 curl——本机 3002/3003 对 curl 请求一律 400，
   2026-08-15 实测，原因未查但生产客户端不受影响）发 claude-opus-5，
   consume 日志 `channel_id=57` 且 `channel_affinity.rule_name="claude trace"`；
3. guardian 下一周期 critical-models 告警解除。

## 回退

ch76 恢复：`update channels set status=1 where id=76` + `/api/channel/fix` +
测试通过后 `status=2 where id=57`（或保留双渠道并行，权重 ch76=20 vs ch57=3
天然主从）+ 再一次 `/api/channel/fix`。亲和自动回钉 ch76。
