# NewAPI opus 聚合不可用根因与处置（2026-09-06）

## 用户报告
"opus 模型聚合经常用不了"

## 根因链（从外到内）

1. **11/16 个 opus 渠道全部死亡**（实测每个上游）：
   - ch3 baibei-100xlabs：403 error 1010（Cloudflare 封禁我们 IP/ASN）
   - ch9 linxi-k40、ch18 linxi-k40-backup：403 "API Key is not assigned to any group"（上游 admin 删了 key 分组）
   - ch57 gorouter：403 error 1010（Cloudflare 封禁）
   - ch72 anyrouter：上游 429 过载（org 级限流,见 recover-poll）
   - ch75/97/98/99 tabitoken×4：auto_ban=1（403 CF 页）
   - ch82 7758：auto_ban=1
   - ch123 zzzcoding：Codex-only 门（Claude 已死）

2. **健康只剩 3 个**：
   - ch93 sotamodel（主）：6h 承载 242 次 = **86% 流量**, avg 21s
   - ch116 kktoken：39 次（健康但闲置）
   - ch86 agentrouter-claude：20 次 **全是 0 token 空响应**（200 + completion_tokens=0, use_time=0, prompt=58261 固定——上游返回 200 但空 content）

3. **ch86 是"假成功"污染源**：
   - Guardian 空响应率 0.7% → 14% 的主要贡献者
   - NewAPI 把它算成功（HTTP 200）,用户看到空回复
   - fallback 路由以为成功,不会重试下一个渠道
   - **已于 2026-09-06 21:25 禁用**（status=2）

4. **单点过载**：ch93 一旦抖动,整个 opus 聚合就不可用

## 处置

```bash
# 禁用污染源 ch86（已执行）
sqlite3 ~/.new-api-local/new-api.db "UPDATE channels SET status=2 WHERE id=86"
```

## 验证

```bash
omp -p --model zg-newapi-anthropic/claude-opus-5 "Reply with exactly one word: pong"
# 53s pong ✓
```

## 未解决（需后续观察/处理）

- **ch93 单点**：如果它挂,opus 池只剩 ch116 kktoken——**没有第三备份**。建议：
  - 提高 ch116 weight（当前 5, ch93 weight=1——优先级已偏 ch116,但流量没过去——可能有别的路由逻辑）
  - 或增加新渠道
- **ch87 channel test timeout**（Guardian 21:17 实测）——ch87 是什么模型需查
- **上游 org 级限流**（anyrouter/ch72）——recover-poll 在守

## 工具/查询模板

```python
# 渠道健康 24h 分布
import sqlite3, time
c = sqlite3.connect(r'C:/Users/zhugu/.new-api-local/new-api.db')
c.execute("""SELECT channel_id, COUNT(*) n, AVG(use_time) FROM logs
  WHERE type=2 AND model_name LIKE '%opus%' AND created_at > ? GROUP BY channel_id""",
  (time.time()-86400,)).fetchall()

# 找空响应污染源
c.execute("""SELECT channel_id, COUNT(*) FROM logs
  WHERE type=2 AND model_name LIKE '%opus%' AND completion_tokens=0
  AND created_at > ? GROUP BY channel_id""", (time.time()-21600,)).fetchall()

# 实测上游渠道（用 DB 里的真 key,注意多 key \n 分隔取第一个）
# type=14 走 /v1/messages + x-api-key；type=1 走 /v1/chat/completions + Bearer
```

## 教训

- **auto_ban=0 status=2 不等于"误禁"**——这次 6 个手动禁用的全是真死（上游 403）
- **`use_time=0 + completion_tokens=0` 是上游空响应特征**——NewAPI 不自动检测,Guardian 的"空响应率"是对的监控
- **单渠道承载 80%+ 流量是可用性炸弹**——一旦它空响应/超时,整组感觉"不可用"
