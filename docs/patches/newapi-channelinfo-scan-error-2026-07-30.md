# NewAPI channel_info NULL 致每分钟同步报错（channel_cache.go:34）（2026-07-30）

**Date:** 2026-07-30 ~22:20 CST
**Status:** 已变更并实时验证 — 仅回填 VPS NewAPI DB `channel_info` 列的 5 个 NULL 行，未改 schema，未碰 cc-switch

## 1. 问题（实况发现，非旧文档）

容器日志每 60s（每次渠道缓存同步）刷一条：

```
/build/model/channel_cache.go:34 sql: Scan error on column index 28, name "channel_info": unexpected end of JSON input
[SYS] ... | channels synced from database
```

- 列索引 28 = `channels.channel_info`（`ChannelInfo` 结构体，`gorm:"type:json"`）。
- DB 里有 **5 行**该列为 `NULL`：`29 xiaoxiaobai-grok-tailscale`、`30 fastaitoken-gpt`、`31 aliyun-qwen38`、`33 kimi-official-k3`、`34 4router-gpt`。

## 2. 根因

`InitChannelCache()` 用 `DB.Find(&channels)` 全表扫描，逐行 Scan `channel_info`。`ChannelInfo.Scan`（`model/channel.go:170`）无 nil/空守卫：

```go
func (c *ChannelInfo) Scan(value interface{}) error {
	bytesValue, _ := value.([]byte)      // NULL 列 → 类型断言得 nil
	return common.Unmarshal(bytesValue, c) // Unmarshal(nil) → "unexpected end of JSON input"
}
```

NULL 列 → `bytesValue` 为 nil → `common.Unmarshal(nil, c)` 直接报错。每次同步命中这 5 行报 5 条（GORM 收集 scan error 后继续，`channels synced` 仍打印）。

## 3. 影响评估（诚实）

**基本是日志噪声，不是路由故障。**

- 5 个受影响行全部是 **单 key 渠道**（`is_multi_key=0`）：Scan 失败时 `channel_info` 留零值 `ChannelInfo{is_multi_key:false}`，恰好等于单 key 渠道的正确值；渠道其余字段（key/models/status/weight）照常 Scan，路由不受影响。
- 多 key 渠道（`3/14/15/32`）`channel_info` 一直是完整 BLOB，从未 NULL —— 只有它们 NULL 才会真丢多 key 轮询状态。
- 实测同步窗口内路由健康：5min 内 43 次 `200`、0 次 4xx/5xx。

所以这是「该修但不紧急」：回填让数据与其他单 key 行一致、消掉每分钟的误导性 ERROR、且**重启安全**（否则容器重启后噪声重现）。

## 4. 修复

回填规范单 key 值（与其他单 key 行 / Go `ChannelInfo.Value()` 对零值的 Marshal 一致）。

改前备份：`/opt/new-api/data/backups/one-api.before-channelinfo-fix-20260730-210716.db`

```sql
PRAGMA busy_timeout=8000;
UPDATE channels
SET channel_info = '{"is_multi_key":false,"multi_key_size":0,"multi_key_status_list":{},"multi_key_polling_index":0,"multi_key_mode":"random"}'
WHERE channel_info IS NULL;   -- 命中 29,30,31,33,34
```

`MEMORY_CACHE_ENABLED=true`，同步从 DB 周期拉取，无需 `podman restart`。

## 5. 验证（实时，2026-07-30 22:20 CST）

- 磁盘：22 行 `channel_info` —— NULL=0、空串=0、非法 JSON=0；`journal_mode=delete`（无 WAL）。
- 5 个回填行：`is_multi_key=0`、`len=122`（规范 blob）。
- 日志：最后一条 scan error 停在 **21:28:52**，此后 ~51min / ~51 次同步全 clean；近 3min ERROR=0、SYNC=3。

> 备注：回写（21:07）到报错停止（21:28）有约 20min 滞后 —— 运行中的 Go 进程持旧连接/读快照，待连接池回收后才反映（`journal_mode=delete`，无 WAL 可 checkpoint）。[推测] 磁盘数据现已正确，故重启安全。

## 6. 回滚

```sh
podman stop new-api   # journal_mode=delete，停机再拷贝
cp /opt/new-api/data/backups/one-api.before-channelinfo-fix-20260730-210716.db /opt/new-api/data/one-api.db
podman start new-api
```

## Related

- 边界：`docs/ops/do-not-modify-cc-switch.md`
- 同日改动：`docs/patches/grok45-ch17-reenable-2026-07-30.md`
- 状态基线：`docs/ops/newapi-vps-minimal-state-2026-07-28.md`

> 安全：本文档不含 VPS 密码、NewAPI admin token、用户 token。
