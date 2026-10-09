# NewAPI DeepSeek V4 Flash 聚合（2026-10-08）

## 目标

把 NewAPI（`127.0.0.1:3002`，`C:/Users/zhugu/.new-api-local/new-api.db`）中所有可用 DeepSeek 渠道聚合为
`deepseek-v4-flash` 的可靠路由池，消除单一渠道 429 限流 / 宕机导致不可用的问题。

## 盘点结论（全库 SQL 扫描 34 个含 deepseek 引用渠道 + 37 路并发直连探针）

| 分类 | 渠道 | 说明 |
|---|---|---|
| ✅ 主 | ch180 agentrouter | `deepseek-v4-flash` 本体，健康，接受 `max_tokens=131072` |
| ✅ 备 | ch118 seeseed | `deepseek-v4-flash` 本体，复活（原 auto_ban=1 由 ReadTimeout 触发） |
| ✅ 末 | ch15 sensenova | `deepseek-v4-flash` 本体 + 1M 上下文；429 tpm/rpm 限流，作末位兜底 |
| ✅ vision 池 | ch140-146/147 intern-discovery | 仅 `deepseek-v4-flash-vision`（本体 404 `not supported by TokenPlan`） |
| ❌ 死 | ch110 yjs（403）、ch148 budsin（403 CF 1010）、ch149 nimbridge（502）、ch150-170 tokenrhythm 21 渠（402）、ch172 42x（403 CF 1010） | 维持停用 |

关键探测：intern-discovery 系（140-147）接本体 `deepseek-v4-flash` 一律 404 `not supported by TokenPlan`，
只能进 vision 池。

## 最终池配置（channels + abilities 两表）

- ch180 agentrouter：`status=1 auto_ban=0 priority=51 weight=5`（主）
- ch118 seeseed：`status=1 auto_ban=0 priority=30 weight=5`（备）
- ch15 sensenova：`status=1 auto_ban=0 priority=25 weight=1`（末位，1M 能力，429 限流故压到 ch118 之后）
- vision：ch140/141/142/143/146/147 均 `enabled=1 priority=40`（ch147 复活，第 6 渠）

## 实施方式

本版本 NewAPI `PUT /api/channel/` 与 `PUT /api/channel/{id}` 均 404，无渠道更新 API → 走 SQLite 直改先例
（先备份，后 UPDATE 两表，channel_cache 约 1 分钟自动同步，无需重启）。

- 备份：`C:/Users/zhugu/.new-api-local/backups/new-api-before-deepseek-pool-v2-20261008-151305.db`
- 首次聚合（v1）备份：`new-api-before-deepseek-pool-adjust-20261008-144253.db`、`...144351.db`

## 验证证据

- 直连探针：ch180 `deepseek-v4-flash` 200；ch118 200；ch15 200（429 风暴已过）；140-147 vision 200。
- 端到端（经 NewAPI `/v1/chat/completions`，OMP models.yml `zg-newapi` key）：
  - `deepseek-v4-flash` 并发 6 请求 → **6/6 HTTP 200**（2.85~3.94s）
  - `deepseek-v4-flash-vision` → **200**
- 日志：`C:/Users/zhugu/.new-api-local/logs/oneapi-20261008111259.log` 中 11:55:30/11:55:51（UTC）请求
  `channel_id=180` relay 200（新流量主落 ch180）。

## OMP 侧归一

`C:/Users/zhugu/.omp/agent/models.yml` L8 `zg-newapi/deepseek-v4-flash`：

- name：`DeepSeek V4 Flash (NewAPI 聚合池: ch180 主 / ch118 备 / ch15 末)`
- contextWindow：`1000000 → 262144`（对齐主渠 ch180 实测能力，避免 1M 申报导致 262K~1M 输入失败）
- maxTokens：`131072` 保留（ch180 实测接受）
- efforts：`[low, medium, high, xhigh] → [low, medium, high, xhigh, max]`（2026-10-08，备份
  `~/.omp/agent/backups/models.yml.bak-20261008-flash-efforts-max`）。依据与生效面见下节。
- 新增注释说明聚合池构成与申报依据

### 2026-10-08 晚：全库 compactionModel 换轨（A1 处置，用户授权）

原全局 `compactionModel: zg-newapi/omen-alpha` **断链**——唯一承载 ch125
（opencode-go 免费档）实测 429 `GoUsageLimitError`（窗口限额）被 auto_ban 禁用
（ch130 space-bunny 同态）。130 处活引用 + 1 处注释模板整体替换为
`zg-newapi/deepseek-v4-flash`（本聚合池）。

- 备份：`~/.omp/agent/models.yml.bak-20261008-compaction-omen2ds`
- 验证：YAML 解析 OK、`test_omp_routes` 40/40 OK、compaction 目标网关实弹 200/3.5s
  （含 reasoning 面，content 正常）
- omen-alpha 条目本身保留在 models.yml（死条目批量摘除属 B 类，另行处置）

## 思考强度（effort）口径

官方（`api-docs.deepseek.com/zh-cn/guides/thinking_mode`、`news/news260424`）：V4-Flash
思考强度参数为 `reasoning_effort`，可用 `low/high/max`，`minimal/low→low`、`medium/high/xhigh→high`、
`ultra→max`；默认 `high`，官方建议 agent 场景用 `max`。

三跳现状（2026-10-08 实测）：

| 跳 | `reasoning_effort` 处置 | 实测 |
|---|---|---|
| ch180 主（agentrouter，经 8788 桥） | NewAPI 渠道 `param_override` = `{"operations":[{"path":"reasoning_effort","mode":"delete"}]}` | 桥直连 `high/xhigh/max/ultra` 全 200；删字段后同样 200 |
| ch118 备（seeseed） | 透传 | 直通 `low/medium/high/xhigh/max` 全 200 |
| ch15 末（sensenova） | `param_override` 条件钳制 `max → xhigh`（见 `scripts/ops/clamp_ch15_reasoning_effort.py`） | 该上游白名单仅 `low/medium/high/xhigh/none` |

**已知取舍**：主链 ch180 会在出网前删掉 `reasoning_effort`（沿用内容过滤规避决策，见
`agentrouter-content-filter-false-positive-2026-08-21.md`），故主链上选档位不改变上游请求体——
档位差异实际只在 ch118/ch15 备链、或 ch180 override 撤销后才生效。回放 400 抓包体（696KB、
12 tools、stream）经 NewAPI 与桥直连均 200，400 已非现网复现项。

**override 生效性量化实证**（2026-10-08 20:2x，同一 prompt `Reply with exactly:`，`max_tokens=600`，
每档 2 次）：

| 下发路径 | `reasoning_effort=minimal` | `reasoning_effort=max` |
|---|---|---|
| 桥直连 8788（字段到达上游） | 思维链 51 / 75 字符，19 / 25 token | 思维链 **541 / 236 字符，139 / 56 token** |
| 经 NewAPI 3002（ch180 override 删字段） | 70 / 53 字符 | **53 / 63 字符**（与 minimal 无差别） |

结论修正：**上游是认档位的**（直连 max 约为 minimal 的 6~10 倍思维量），此前的"上游对档位不敏感"
判断是简单题上小样本噪声（以及 3000-token 饱和题把各档位都顶满预算）造成的误读。因此：
不撤 override ⇒ 主链实质固定在**上游默认档**（官方口径 `high`），既升不到 `max` 也降不到 `low`；
关闭思考的旁路同样不通——`thinking:{"type":"disabled"}`、`enable_thinking:false` 在该上游均无效
（实测仍返回 `reasoning_content`）。用户 2026-10-08 裁决：**保持现状（不撤 override）**。

验证：`omp models` 生效白名单含 `max`；`omp -p --model zg-newapi/deepseek-v4-flash:max`
→ 回复目标串、日志全部落 ch180 `type=2`（成功）；`python3 -m unittest scripts.ops.test_omp_routes`
→ 40/40 OK。

## 注意事项

- 429 不会触发 NewAPI 渠道 failover（实测 429 直接透传给客户端），因此限流渠道必须压到健康渠道之后。
- ch15（1M）仅在 ch180+ch118 双失败时兜底；大上下文（>262K）请求无法靠 ch180 承载，需 OMP 侧在
  contextWindow 内紧凑（已通过 262144 申报实现）。