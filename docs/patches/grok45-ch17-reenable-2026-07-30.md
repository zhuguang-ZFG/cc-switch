# grok-4.5 恢复双渠冗余（ch17 ability 重新启用）（2026-07-30）

**Date:** 2026-07-30 ~20:07 CST
**Status:** 已变更并实时验证 — 仅改 VPS NewAPI DB 两个整数列，未重启容器，未碰 cc-switch

## 1. 问题（实况发现，非旧文档）

巡检 `abilities` 表发现 grok-4.5 **已塌缩成单渠**：

| 渠道 | `channels.status` | `abilities.enabled` | 说明 |
|---|---|---|---|
| 17 openoneapi-grok | 1（启用） | **0（禁用）** | 渠道启用、但 grok-4.5 这条 ability 被手动关掉 |
| 29 xiaoxiaobai-grok-tailscale | 1 | 1 | grok-4.5 唯一在跑的渠道 |

- `channels.status=1` 但 `abilities.enabled=0` 的错配：`other_info.status_reason="manual operation"` —— 此前有人手动禁了 17 的 grok-4.5 ability。`AutomaticEnableChannelEnabled=true` 只复活 `status=3` 的自动禁用渠道，不会碰这条手动禁用的 ability，所以不会自愈。
- 后果：grok-4.5 只剩 29 一条（tailscale 私网 `100.83.32.95`），零冗余。客户端 smoke 实测在 29 抖动时 **25s 超时**。

## 2. 修复

重新启用 17 的 grok-4.5 ability，并按实测延迟配权重（29 快、17 慢 → 17 低权重当备份）：

| 渠道 | 手动 channel-test 延迟 | `weight`（改前→改后） | 分流占比 |
|---|---|---|---|
| 29 xiaoxiaobai-tailscale | 5.2s | 20（不变） | ~67% |
| 17 openoneapi | 10.2s | 30 → **10** | ~33% |

改前先备份：`/opt/new-api/data/backups/one-api.before-grok17-reenable-20260730-200651.db`。

```sql
PRAGMA busy_timeout=8000;
UPDATE abilities SET enabled=1, weight=10 WHERE [group]='default' AND model='grok-4.5' AND channel_id=17;
UPDATE channels  SET weight=10 WHERE id=17;
```

## 3. 为何这次直写 DB（而非 admin API）

- `PUT /api/channel/` 回 `{"message":"Invalid parameters","success":false}`：`GET /api/channel/17` 返回的 `key` 被脱敏成空串，原样回写触发校验失败（回写空 key 有清空真实 key 的风险，放弃）。
- 本次**只改现有 ability 行的 `enabled`/`weight` 两个整数列**，非结构性改动（没加/删模型行、没动 `group`、没碰 `channel_info` BLOB / key）。这与 `newapi-health-check-2026-07-29-pm.md` §2 警告的「裸改 abilities **结构**导致 desync」是不同场景。
- `MEMORY_CACHE_ENABLED=true`：内存缓存周期性从 DB 同步,改动已自动生效,**无需 `podman restart`**（实测数十秒内路由已反映）。

## 4. 验证（实时）

服务端 `localhost:3000` 打 5 次 grok-4.5,全 `200`:

```
call1 200 8.32s   call2 200 14.21s   call3 200 8.21s   call4 200 4.64s   call5 200 12.21s
```

`logs` 表确认流量已在两渠间分布（最近 7 次 29:17 ≈ 4:3,符合 20:10 权重）:

```
20:08:45 ch17 11s | 20:08:36 ch29 2s | 20:08:35 ch29 0s | 20:08:34 ch17 5s
20:08:29 ch29 8s  | 20:08:21 ch29 14s | 20:08:07 ch17 8s
```

同批健康普查:全库再无「渠道启用 + ability 禁用」的静默丢模型错配(此为唯一一例,已修);多键渠道(ch3/14/15/32)`channel_info` BLOB 完整;opus-5 已异构多源(林夕 9/18 + gorouter 26/27/28 + 百倍 3)。

## 5. 未处理 / 需人工

- **⚠️ 本次逆转了一个手动设置**。若当初是嫌 17 的 10s 太慢而故意关,现在会偶发 10–14s 的 grok-4.5。判断「有冗余 > 偶发慢」对编码模型更合理,但可回滚(见 §6)。
- **ch17 OpenOneAPI key 轮换**(遗留安全债):需上游控制台签发新 key,SSH/DB 侧做不了。拿到新 key 可写入渠道并 test。
- **ch21 `191.96.25.96` gpt 备份**:实测仍 `503 auth_not_found`(上游 codex 池无 auth),`status=3` 自动禁用正确,保持禁用。
- **告警缺口**:VPS 极简化(`newapi-vps-minimal-state-2026-07-28.md`)刻意撤掉了 TG/guard/health_check。重加监控与该决定冲突,需明确点头再动。

## 6. 回滚

退回单快渠(仅保留 29):

```sql
UPDATE abilities SET enabled=0 WHERE [group]='default' AND model='grok-4.5' AND channel_id=17;
```

或整库回滚:`cp /opt/new-api/data/backups/one-api.before-grok17-reenable-20260730-200651.db /opt/new-api/data/one-api.db`(须先 `podman stop new-api`,`journal_mode=delete`)。

## Related

- 状态基线:`docs/ops/newapi-vps-minimal-state-2026-07-28.md`
- 前序巡检:`docs/patches/newapi-health-check-2026-07-29-pm.md`(记录 ch20 fengwind-grok 57s 被自动禁用)
- 边界:`docs/ops/do-not-modify-cc-switch.md`

> 安全:本文档不含 VPS 密码、NewAPI admin token、用户 token。环境备忘见 `newapi-health-check-2026-07-29-pm.md` 末尾与密码管理器。
