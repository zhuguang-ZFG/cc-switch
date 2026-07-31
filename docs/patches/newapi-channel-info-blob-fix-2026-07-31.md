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

NewAPI 的 `ChannelInfo.Scan` 实现只接受 `[]byte`：

```go
bytesValue, _ := value.([]byte)
return common.Unmarshal(bytesValue, c)
```

SQLite driver 对 BLOB 返回 `[]byte`，但对 TEXT 返回字符串；类型断言失败后 `bytesValue` 为 `nil`，最终触发 `unexpected end of JSON input`。因此即使 TEXT 内容合法且 `json_valid()=1`，仍会 Scan 失败。

本次是用 Python `sqlite3` 直写 `channel_info` 时绑定了 `str`：`str` 存为 TEXT，`bytes` 或 `sqlite3.Binary(...)` 才存为 BLOB。SQLite CLI 的普通字符串字面量同样存为 TEXT，工具本身不是区别，显式写入 BLOB 才是关键。

## 3. 修复

### 3.1 停机与备份

直接修改 NewAPI SQLite 前先停止容器并备份；不要在运行中的进程仍可能写库时操作。

```sh
DB=/opt/new-api/data/one-api.db
BACKUP="/opt/new-api/data/backups/one-api.before-channel-info-blob-$(date +%Y%m%d-%H%M%S).db"

podman stop new-api
cp -- "$DB" "$BACKUP"
printf 'backup: %s\n' "$BACKUP"
```

### 3.2 检查并修复

先检查异常行。除本次已知的 ch15/40/41 外若还有结果，尤其是多 key 渠道或非法 JSON，不要套用单 key 默认值，应先逐行确认其真实配置。

```sh
sqlite3 "$DB" <<'SQL'
.headers on
.mode column
SELECT id, name, typeof(channel_info) AS storage_type,
       json_valid(channel_info) AS valid_json
FROM channels
WHERE channel_info IS NULL
   OR typeof(channel_info) <> 'blob'
   OR json_valid(channel_info) <> 1;
SQL
```

本次等价修复如下：仅在 ch40 仍为 NULL 时回填规范单 key JSON，再把 ch15/40/41 中合法的 TEXT 原样转为 BLOB。条件不匹配时不会覆盖现有值。

```sh
sqlite3 "$DB" <<'SQL'
.bail on
PRAGMA busy_timeout=8000;
BEGIN IMMEDIATE;

UPDATE channels
SET channel_info = CAST('{"is_multi_key":false,"multi_key_size":0,"multi_key_status_list":{},"multi_key_polling_index":0,"multi_key_mode":"random"}' AS BLOB)
WHERE id = 40 AND channel_info IS NULL;

UPDATE channels
SET channel_info = CAST(channel_info AS BLOB)
WHERE id IN (15, 40, 41)
  AND typeof(channel_info) = 'text'
  AND json_valid(channel_info) = 1;

COMMIT;
SQL
```

当前主机也有规范修复脚本 `/opt/new-api/repair_channel_info.py`；后续全库修复应在停机、备份后优先复用该脚本，不再临时编写不同语义的写库脚本。

### 3.3 验证与启动

以下检查必须分别返回“零行”和 `ok`，否则不要启动容器，应回滚备份并调查残留行。

```sh
sqlite3 "$DB" <<'SQL'
SELECT id, name, typeof(channel_info), json_valid(channel_info)
FROM channels
WHERE channel_info IS NULL
   OR typeof(channel_info) <> 'blob'
   OR json_valid(channel_info) <> 1;
PRAGMA integrity_check;
SQL

podman start new-api
```

启动后确认容器日志无 `channel_info` Scan error、`GET /api/channel` 返回渠道数据且前端列表恢复；再调用一个代表性 relay 模型确认转发仍为 200。本次验证结果均通过，`channels synced from database` 正常。

### 3.4 回滚

若检查、启动或管理面验证失败，停止容器并恢复上一步打印的备份路径：

```sh
podman stop new-api
cp -- /opt/new-api/data/backups/one-api.before-channel-info-blob-<timestamp>.db /opt/new-api/data/one-api.db
podman start new-api
```

## 4. 注意

- 写库工具无关，关键是显式写入 BLOB：Python 使用 `bytes`/`sqlite3.Binary(...)`；SQLite CLI 使用 `CAST(... AS BLOB)` 或 BLOB 字面量。写后必须检查 `typeof(channel_info)='blob'`。
- 新渠道必须设置合法 JSON BLOB。NULL、TEXT 或非法 JSON 都会触发 `ChannelInfo.Scan` 错误，并可能使整个 `/api/channel` 列表读取失败。
- 本次三个异常渠道均为单 key，实测 relay 未受影响；不能据此推断所有 Scan 错误都不影响 relay。多 key 渠道若丢失 `channel_info`，会破坏 key 轮询状态。

> 安全：本文档不含 API key、VPS 密码。
