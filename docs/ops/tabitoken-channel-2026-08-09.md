# tabitoken 聚合渠道接入（2026-08-09）

在本地 NewAPI（127.0.0.1:3002）新增 **ch75 `tabitoken`** 聚合渠道：3 个 key 轮询，上游 `https://tabitoken.com`（one-api/new-api 系中转，Anthropic 计费语义，支持 OpenAI/Anthropic 端点）。

## 渠道参数

| 项 | 值 |
|----|----|
| id / name | 75 / `tabitoken` |
| type | 14（Claude relay，与 ch3/ch9 一致） |
| base_url | `https://tabitoken.com` |
| key | 3 枚换行分隔（multi-key polling，`channel_info` 为 BLOB） |
| models | `claude-opus-5, claude-opus-5-thinking, claude-opus-4-8, claude-opus-4-8-thinking` |
| model_mapping | `{"zg-claude-opus-5":"claude-opus-5"}` |
| priority / weight | 50 / 5（新源小权重入 opus 池，ch3(51) 仍独占顶层） |
| status | 1 enabled，auto_ban=1 |
| 定价 | `claude-opus-5-thinking` / `claude-opus-4-8-thinking` 补 `ModelRatio=0.5`、`CompletionRatio=2`（对齐基础模型） |

## 前置：代理路由（关键）

tabitoken.com 在本机**直连被 GFW SNI 重置**（TLS ClientHello 后 RST，39 字节即断；HTTP 80 走 502）。云端实测站点正常（r.jina.ai 抓取成功，标题 "New API"）。

现状与机制（2026-08-09 复核）：

- **当前连通由激活配置尾部 `MATCH,悍刀行` 保证**——所有未匹配流量全部走代理组，tabitoken 实测 200。无显式规则时也不受影响。
- **规则已写入正确机制**：Clash Verge Rev 2.5.2 的 merge 文件 `prepend.rules` **不生效**（prepend 被嵌为惰性顶层键，只做 key 覆盖；规则需走 per-profile rules 增强）。已写入激活配置（悍刀行）的 rules 增强 `profiles\r5GaE5Bsk4Aa.yaml`：
  `prepend: ['DOMAIN-SUFFIX,tabitoken.com,悍刀行']`
  - 目标必须是**真实组名 `悍刀行`**，`PROXY` 不是有效目标（无该组，mihomo 校验会报 proxy not found 导致配置不加载）
  - 该文件在下一次 verge 配置重载（订阅每日更新 / 手动应用 / 编辑规则保存）后生效；本次会话无重载触发（touch 配置只触发 tray sync，非 reload）
- 曾误写入 `Merge.yaml`（全局模板）与 `mMv4S8X7mC8v.yaml`（激活配置 merge）的 `DOMAIN-SUFFIX,tabitoken.com,PROXY` 已撤回——两者在 2.5.2 上均为惰性配置，且 PROXY 目标无效。Merge.yaml 中既有的 sharedchat/linux.do/cloudflare 等 PROXY 规则同理为惰性历史遗留，未在本会话范围内处理
- 若日后更换激活配置，需在新的 rules 增强里保留 tabitoken 规则（或依赖该配置的 MATCH 走代理），否则换到 MATCH,DIRECT 的配置时 tabitoken 会被 GFW 重置

## 验证

- 3 枚 key 直连 `POST /v1/chat/completions`（claude-opus-5）全部 200
- **上游身份边界**：响应头 `x-new-api-version: v1.0.0-rc.23` + `x-oneapi-request-id` 表明 tabitoken 是 one-api/new-api 系中转；`usage_source=anthropic` / `msg_` ID / `billing_usage.claude_usage` 均为 relay 自报口径，**不能证实上游是 Anthropic 官方直连**（可能是官方 key、第三方 Claude 中转、或模型伪装）。能证实的是：协议形态为 Anthropic 计费语义、三 key 有效、链路可用
- `GET /api/channel/test/75` → 200，4.5s
- 网关 e2e（用户令牌走 127.0.0.1:3002）：
  - `claude-opus-5` → 200（2.8s）
  - `zg-claude-opus-5` → 200（31.1s，mapping 生效；首跳偏慢，上游波动）
  - `claude-opus-5-thinking` → 200（1.4s，定价已生效）

## 本次踩到的 fork API 细节（备忘）

- 建渠道：`POST /api/channel/` body 必须 `{"channel": {...}, "mode": "multi_to_single"}`；裸 body 报 `channel cannot be empty`，缺 mode 报 `不支持的添加模式`。is_multi_key 仅此路径可置 true
- **`multi_key_status_list: null` 会卡死轮询**：API 创建的多 key 渠道 channel_info 里 status_list 是 `null`（ch3/ch9 手工写入的是 `{}`），轮询器不推进 index，所有请求钉死在 key1。修复：DB 直写 `UPDATE channels SET channel_info=CAST(? AS BLOB)` 改为 `{}` + 无害 PUT 触发缓存刷新
- 轮询 index 写回 DB 的时机：渠道测试（`GET /api/channel/test/:id`）会写回；普通请求只推进内存 index 不写 DB（ch3 大量流量后 index 才 4 佐证）
- `channel_info` 里 `multi_key_mode` 创建时不落库，需随后 `PUT /api/channel/` 顶层带 `multi_key_mode: "polling"` 覆写（PUT body 不能含 `status`，key 被脱敏需显式重塞）
- 渠道测试是 `GET /api/channel/test/:id`（POST 返回 404 Invalid URL）
- option 写入是 `PUT /api/option/` body `{"key": ..., "value": ...}` 单条格式（guardian.py `exclude_retry_status_code` 同款）；map/数组/`{options:[...]}` 格式返回 success 但**不落库**
- ch75 `channel_info` 存储类型为 BLOB（避开 TEXT 行 cache 同步坑）

## 三 key 轮询验证（2026-08-09 补充）

直连阶段三 key 各独立补全 200；修复 status_list 后连续 5 次渠道测试，DB index 0→1→2→0→1→2 循环推进，每次 200——key1/key2/key3 均被 NewAPI 轮询实际使用且全部有效。普通请求期间 index 不落库属 fork 正常行为，以渠道测试序列为准。

## 回滚

- 渠道：`POST /api/channel/75/status {"status":2}`（与 guardian.py `disable_channel` 同款）
- 代理规则：删 `r5GaE5Bsk4Aa.yaml` 中 tabitoken 行，下次配置重载后生效
- 定价：`PUT /api/option/` 从 ModelRatio/CompletionRatio 移除两枚 -thinking 条目
