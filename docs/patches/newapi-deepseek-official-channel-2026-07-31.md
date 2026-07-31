# NewAPI DeepSeek 官方直连渠道（ch42，2026-07-31）

## 1. 背景

deepseek-v4-flash 此前有多个中转源（ch15 sensenova、ch35 cline-free、ch40 0v0、ch126 vyceai），但缺官方直连。官方 API 延迟低（~0.8s）、稳定性高、无中转层限流/断供风险。`deepseek-v4-pro` 此前仅 ch36 step-router-v1 中转可达，本次补官方直连第二源。

**官方渠道与聚合渠道分开**：ch42 用 `model_mapping` 把官方别名 `deepseek-official-v4-flash` / `deepseek-official-v4-pro` 映射到上游真名，客户端发官方别名只走 ch42，发裸名 `deepseek-v4-flash` 只走聚合池（ch15/40/126）。避免官方/中转混合调度打散缓存、且可按需选源。

## 2. 接入配置

实际落地用 **ch42**（max id=41，42 空闲）。停容器后 sqlite 直写（rc.21 `POST /api/channel` 会 panic；`channel_info` 必须为 BLOB 才不被 `ChannelInfo.Scan` 报错）。

> 以下 SQL 为**落地后记录**（含 §4.2 修正后的定价注释）。重跑需按 `newapi-channel-info-blob-fix-2026-07-31.md` 流程（停机/备份/校验/回滚）并补齐渠道必填列（`used_quota`、`created_time` 等），且定价以 §4.2 为准。

```sql
-- 备份：cp /opt/new-api/data/one-api.db /opt/new-api/data/backups/one-api.before-ch42-official-split-<ts>.db
-- 停容器：podman stop new-api

-- 渠道：models 用官方别名，model_mapping 映射到上游真名
INSERT INTO channels
  (id, name, type, base_url, key, models, status, priority, weight, "group", auto_ban, channel_info, model_mapping)
VALUES
  (42, 'deepseek-official', 1, 'https://api.deepseek.com',
   '<redacted>',
   'deepseek-official-v4-flash,deepseek-official-v4-pro',
   1, 50, 10, 'default', 1,
   CAST('{"is_multi_key":false,"multi_key_size":0,"multi_key_status_list":{},"multi_key_polling_index":0,"multi_key_mode":"random"}' AS BLOB),
   '{"deepseek-official-v4-flash":"deepseek-v4-flash","deepseek-official-v4-pro":"deepseek-v4-pro"}');

-- abilities：官方别名，与聚合裸名隔离
INSERT INTO abilities ("group",model,channel_id,enabled,priority,weight,tag) VALUES
  ('default','deepseek-official-v4-flash',42,1,50,10,''),
  ('default','deepseek-official-v4-pro',42,1,50,10,'');

-- 定价：官方别名按 DeepSeek 官方价（见 §4.2 修正）
-- ModelRatio: flash=0.07 pro 未定（官方 miss $0.14/M，比例尺 0.5↔$1/M）
-- CompletionRatio: flash=2 pro=2（0.28/0.14）
-- CacheRatio: flash=0.02（hit $0.0028/M = 2%×miss）
-- CreateCacheRatio: flash=1.0（DeepSeek 无缓存写入加价）

-- 启容器：podman start new-api
```

备份：
- `one-api.before-ch42-deepseek-20260731-181122.db`（首次插入）
- `one-api.before-ch42-official-split-20260731-182419.db`（拆分前）

## 3. 验证

```text
channels.id=42  name=deepseek-official  typeof(channel_info)=blob  json_valid=1
  models=deepseek-official-v4-flash,deepseek-official-v4-pro
  model_mapping={"deepseek-official-v4-flash":"deepseek-v4-flash","deepseek-official-v4-pro":"deepseek-v4-pro"}

abilities ch42: deepseek-official-v4-flash (pri=50, w=10)
            deepseek-official-v4-pro  (pri=50, w=10)
abilities 聚合池: deepseek-v4-flash (ch15 pri=50 w=10, ch40 pri=0 w=0)
```

网关冒烟（固定 UA `kimi-code/1.0`）：

```text
deepseek-official-v4-flash -> 200 OK，0.8s（model 字段返回 deepseek-v4-flash，mapping 生效）
deepseek-official-v4-pro   -> 200 OK，2.3s（model 字段返回 deepseek-v4-pro）
deepseek-v4-flash          -> 200 OK，2.2s（走聚合池 ch15）
```

路由隔离确认（logs 表）：

```text
官方别名请求 -> 全落 ch42（deepseek-official-v4-flash / deepseek-official-v4-pro）
聚合裸名请求 -> 落 ch15/40/126（deepseek-v4-flash）
```

## 4. 渠道亲和

deepseek 亲和规则（`channel_affinity_setting.rules` rule #4）已存在且 enabled：
- 匹配 `^deepseek-.*$`（覆盖官方别名 `deepseek-official-*` 和裸名 `deepseek-v4-*`）
- key 源：`prompt_cache_key`（OMP/Kimi 不发）→ fallback `User-Agent`
- TTL 600s

ch42 自动进入该规则粘滞范围。固定 UA 连发 3 次官方别名全粘滞 ch42。

### 4.1 缓存命中实测（2026-07-31）

同前缀连续两次请求（经 `aliyun.donglicao.com` 官方别名，UA=omp）：

```text
req 1: prompt=2493 cached=0     （首次写缓存）
req 2: prompt=2493 cached=2432  （97.6% 前缀命中；CacheRatio 当时为 0.25，§4.2 已修正为 0.02）
```

近 24h 日志：官方别名请求 100% 落 `use_channel:["42"]`。单渠道 + 单 key + model_mapping 隔离 = 缓存命中无需额外亲和配置（亲和规则 #4 覆盖但无跨渠道可打散）；真正决定命中率的是客户端前缀复用（OMP/Kimi agent 会话天然复用，命中率 ~97%）与 DeepSeek 官方 1h 缓存 TTL。

### 4.2 计费修正：CacheRatio 0.25→0.02（2026-07-31，全量对齐官方价）

**背景**：官方别名初配的定价复制自旧 deepseek-chat 时代的比例（hit/miss = 0.07/0.28 = 0.25）。DeepSeek V4 Flash 官方现行价（api-docs.deepseek.com/quick_start/pricing）：

| 项目 | 官方价 | 比例尺换算 |
|---|---|---|
| 输入 miss | $0.14/M | ModelRatio = 0.07 |
| 输入 hit | $0.0028/M（= 2%×miss） | CacheRatio = 0.02 |
| 输出 | $0.28/M | CompletionRatio = 2（与既有一致） |
| 缓存写入 | 无加价 | CreateCacheRatio = 1.0 |

（比例尺基准：现行 0.5 ↔ $1/M，来自运行计费反推，见下。）

**实测反推（改前，logs 表 quota 逐行吻合）**：运行中实际按 `model_ratio=0.5, cache_ratio=0.25, completion_ratio=2, create_cache_ratio=1.0` 计费。行 `prompt=123383 cache=123264 miss=119 completion=247 quota=15715` = 119×0.5 + 123264×0.5×0.25 + 247×0.5×2 ✓。

**发现 DB 与运行内存漂移**：options 表直写后未重启，DB 中 `CreateCacheRatio=1.25` 而运行进程用旧值 `1.0`——下次任意重启会静默引入 miss 部分 25% 加价。本次一并对齐。

**改动**（备份 `one-api.before-official-flash-pricing-20260731-184412.db`；只动 `deepseek-official-v4-flash` 四项，裸名聚合池/pline 池定价不动）：

```text
ModelRatio      0.5  -> 0.07
CompletionRatio 2    -> 2.0   （不变）
CacheRatio      0.25 -> 0.02
CreateCacheRatio 1.25 -> 1.0
```

改后 `podman restart new-api` 同步。**验证**：options 四项已生效；同前缀请求仍命中缓存（cached=2432，跨重启不受影响）；新计费行 `quota=9`（= 61×0.07 + 2432×0.07×0.02 + 8×0.07×2），同一请求改前为 342.5 → **约 38× 降**。教训：改 options 必须重启，否则 DB 与内存漂移，下次重启计费静默变化。

## 5. 客户端配置

### Kimi Code（`~/.kimi-code/config.toml`）

```toml
# 聚合池（已有，保留）
[models."zg-newapi/deepseek-v4-flash"]
provider = "zg-newapi"
model = "deepseek-v4-flash"
max_context_size = 1048576
capabilities = []
display_name = "deepseek-v4-flash"

# 官方直连别名
[models."zg-newapi/deepseek-v4-pro"]
provider = "zg-newapi"
model = "deepseek-official-v4-pro"
max_context_size = 1048576
capabilities = []
display_name = "deepseek-v4-pro (官方)"

[models."zg-newapi/deepseek-official-v4-flash"]
provider = "zg-newapi"
model = "deepseek-official-v4-flash"
max_context_size = 1048576
capabilities = []
display_name = "deepseek-v4-flash (官方)"
```

### OMP（`~/.omp/agent/models.yml`）

```yaml
      - id: deepseek-v4-flash              # 聚合池
        name: DeepSeek V4 Flash
        contextWindow: 1048576
        maxTokens: 128000
      - id: deepseek-official-v4-pro       # 官方直连
        name: DeepSeek V4 Pro (官方)
        contextWindow: 1048576
        maxTokens: 128000
      - id: deepseek-official-v4-flash     # 官方直连
        name: DeepSeek V4 Flash (官方)
        contextWindow: 1048576
        maxTokens: 128000
```

## 6. 注意

- 官方 API 无内容审核，不会出现中转站常见的 `sensitive_words_detected` / `content-blocked` 拦截
- 定价按 DeepSeek 官方价对齐（ModelRatio 0.07 / CompletionRatio 2 / CacheRatio 0.02 / CreateCacheRatio 1.0，见 §4.2）；`deepseek-v4-pro` 无 prompt caching，未配 CacheRatio，且官方现行 pro 定价未核（miss $0.435/M 参考），保持旧值
- `deepseek-v4-pro` 官方直连（ch42）与 ch36 `step-router-v1`（中转）形成双源，但走不同模型名，互不干扰
- 官方 API key 仅存 VPS DB，不进仓库

> 安全：本文档不含 API key、VPS 密码。
