# NewAPI ch45 救活为 glm-5.3 冗余渠道（2026-09-07）

## 背景
- glm-5.3 聚合此前仅 ch120 单渠道承载（24h 37 次, avg 3.8s, 健康）
- ch45（agentrouter, 同 relay `100.83.32.95:8788`, 多 key 池）因探测模型 gpt-5.6-sol 的
  **402 Budget pool quota exhausted** 被 Guardian 禁用（连坐）
- 实测 ch45 的 glm-5.3 路径活着：200 + 真 text `pong`（8.3s, finish=stop）

## 变更（DB 直写, 快照 `new-api.db.bak-20260907-ch45-glm53`）
```sql
UPDATE channels SET models='glm-5.3', model_mapping='' WHERE id=45;
UPDATE channels SET status=1 WHERE id=45;
UPDATE abilities SET enabled=1 WHERE channel_id=45 AND model='glm-5.3';
DELETE FROM abilities WHERE channel_id=45 AND model != 'glm-5.3';
```
- 砍掉 ch45 上三个 sol 变体（gpt-5.6-sol / zg-gpt-5.6-sol / zg-agent-gpt-5.6-sol）:
  预算池 402 会持续毒化探测; zg-agent-* 是冗余映射
- 副作用: NewAPI 侧 gpt-5.6-sol 聚合入口消失（ch45 是唯一入口）。
  sol 的使用路径: OMP 直连 `agentrouter/gpt-5.6-sol`（models.yml, 实测 pong）

## 验证
- `GET /api/channel/test/45?model=glm-5.3` → 200 `success:true time:6.3s`
  （注意: curl 打 127.0.0.1:3002 会 400——curl 走系统代理（HTTP_PROXY=7897）,
  需 urllib 复刻 Guardian 头（Bearer + New-Api-User: 1）或 curl 加 --noproxy）
- 聚合池: glm-5.3 = ch45(enabled, w=5) + ch120(enabled, w=4), 同 priority 40
- 端到端: `omp -p zg-newapi/glm-5.3` pong ✓

## 监控钩子
- Guardian 会对 ch45 做 full scan（探测模型现在=glm-5.3, 不再触发 402）
- 若 relay 侧 glm-5.3 预算池也耗尽, 两个渠道会一起 402（同 relay 不同 key/池,
  ch45 与 ch120 的 key 独立——一个池枯不影响另一个）
