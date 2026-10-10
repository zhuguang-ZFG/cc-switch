# muyuan-gongyi ch173 双 key 复活（2026-10-10）

ch173 `muyuan-gongyi` 10-09 17:11 因 key 失效被手动停泊（status=2）；用户供三把
新 key，裁决：确认可用则换 key 重新启用入池。qwen3.8-flash-next / qwen3.8-27b
两模型此前 sole source 即 ch173（models.yml 注释），入池即 OMP 可路由。

## Key 判别（逐字实测，不落 key）

muyuan.do 有 Cloudflare 指纹门：探测需浏览器 UA（`Mozilla/5.0 ... Chrome/126`），
否则 1010/403 challenge。`/v1/chat/completions` 双模型实测：

| key（fp=SHA1 前 8 位） | /v1/models | qwen3.8-flash-next | qwen3.8-27b | 处置 |
|---|---|---|---|---|
| `2cd06bc4` | 200（29 模型） | 200 | 200 | 入池 |
| `ab414ae1` | 200（30 模型） | 200 | 200 | 入池 |
| `2d086f24` | 200（2 模型，无 qwen） | 503 `model_not_found` | 503 | 未入池 |

`2d086f24` 鉴权有效但账号绑 muyuan 站侧 "Gemini" 分销组，两 qwen 模型确定性
503（错误体与 10-08 mistral-large-4-0 同型：站侧分组门死，本地无可修缺陷）。

## 换 key 与多 key 形态：channel_info manifest 坑（再犯确认）

本 fork 多 key 语义**不由 `channels.key` 列的换行分隔决定**，而由
`channel_info` BLOB 内 manifest 决定（与 ch127 runbook 的 BLOB 契约同源）：

- 症状：sqlite 直写 `key = "sk-A\nsk-C"` 后渠道测试
  `do request failed: net/http: invalid header field value for "Authorization"`，
  日志 `IsMultiKey: false`——整串带换行的 key 被当单 key 塞进 header。
- 状态开关（`POST /api/channel/{id}/status` 2→1）**不**重载 channel_info，
  需 ~60s 全量缓存同步才生效；判别期间勿误判为 key 又失效。
- 修复：`channel_info` 以 **BLOB** 写
  `{"is_multi_key":true,"multi_key_size":2,"multi_key_status_list":{},"multi_key_polling_index":0,"multi_key_mode":"polling"}`
  （`sqlite3.Binary`，紧凑 JSON；TEXT 写入会炸 distributor，见 ch127 runbook），
  再 status 开关 + 等 60–70s。
- 本 fork PUT 更新端点不可用：`PUT /api/channel/` → `record not found`，
  `PUT /api/channel/{id}` → 404；换 key 只能 sqlite 直写（备份先行）。

## 配置终态

- ch173：status=1，auto_ban=1，p20/w1，key=双 key 轮询（IsMultiKey: true 已日志实证）
- abilities：qwen3.8-flash-next / qwen3.8-27b default 组 enabled=1（未动）

## 验证

- 渠道测试：`GET /api/channel/test/173?model=qwen3.8-flash-next` /
  `?model=qwen3.8-27b` 双 success
- OMP token 经 3002 端到端：flash-next 3/3 200、27b 200；DB 日志归因全部 ch173
- 备份：`~/.new-api-local/backups/new-api-before-muyuan-ch173-repool-20261010-084003.db`
  （sqlite backup API，WAL-safe）

## 回滚

- 停泊：`POST /api/channel/173/status {"status":2}`
- key/manifest 还原：从上述备份恢复 `channels` 行 173（注意同步还原 `channel_info`
  BLOB 与 `key` 两列）

## 备注

- 教训补强：**渠道测试出现 `invalid header field value` = 多 key manifest 缺失**，
  不是上游/鉴权问题，先查 `channel_info` 再动 key。
- 三把 key 全文只经一次性本地临时文件传递（用毕即删），仓内不落。
