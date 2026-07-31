# NewAPI 渠道列表前端"未找到渠道"修复（2026-07-31）

NewAPI 管理后台渠道页显示"未找到渠道，没有可用的渠道"，但 DB 渠道完好（19 enabled）、网关主力模型调用正常。根因：`channel_info` 列存储类型不一致致 GORM Scan 报错。

## 1. 现象与定位

前端渠道管理页空，但 `sqlite3 ... "SELECT count(*) FROM channels WHERE status=1"` 有 19 条。容器日志：

```text
sql: Scan error on column index 28, name "channel_info": unexpected end of JSON input
[SYS] failed to get channels
```

`GET /api/channel` 因序列化报错返回空数据，前端拿到空列表。**网关 relay 不受影响**（relay 路径不经过这个序列化），故主力模型调用仍 200。

## 2. 根因

逐渠道查 `typeof(channel_info)`：

```text
ch15 sensenova-token | text  ← Python sqlite3 写入，存成 TEXT
ch40 0v0-glm        | text  ← 同上（且一度为 NULL）
ch41 zzz-gpt        | text  ← 同上
其余所有渠道        | blob  ← NewAPI 原生创建，存为 BLOB
```

GORM 的 `channel_info` 字段是 `json.RawMessage`（`[]byte`）。SQLite driver 扫描时对 BLOB 正常返回 `[]byte`，对 **TEXT 类型**的 JSON 字符串扫描失败（尽管内容合法、`json_valid()=1`），触发 "unexpected end of JSON input"。

这是用 `python3 sqlite3` 模块直写 `channel_info`（传 str 而非 bytes）导致的——str 存为 TEXT，bytes 才存为 BLOB。

## 3. 修复

把 TEXT 类型的 channel_info 转成 BLOB：

```sql
UPDATE channels SET channel_info = CAST(channel_info AS BLOB) WHERE typeof(channel_info)='text';
```

同时 ch40 的 channel_info 此前是 NULL（也触发 Scan error），已补写合法单 key JSON 再转 BLOB。

改完 `podman restart new-api`，重启后日志无 Scan error，`channels synced from database` 正常，前端渠道列表恢复。

## 4. 注意

- **直写 NewAPI DB 用 sqlite3 CLI 而非 Python sqlite3**：CLI 的 `UPDATE ... SET col='...'` 对 json 列存为 TEXT 也可能触发同样问题；最稳是写完用 `CAST(col AS BLOB)` 归一化类型，或直接用 `x'...'` BLOB 字面量。
- **新渠道务必设 channel_info 合法 JSON 且为 BLOB**：NULL 或 TEXT 都会触发 GORM Scan error，连带整个 `/api/channel` 列表返回空（一个坏渠道影响全部渠道显示）。
- 此问题只影响管理后台渠道列表显示与 channel_cache 同步，不影响 relay 转发。

> 安全：本文档不含 API key、VPS 密码。
