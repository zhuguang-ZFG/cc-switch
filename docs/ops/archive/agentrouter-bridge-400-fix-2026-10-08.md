# agentrouter 桥 400 根因修复 + DeepSeek 聚合收口（2026-10-08）

## 背景

OMP → 本地 NewAPI（`127.0.0.1:3002`）→ ch180 agentrouter（`ps.air-outer.com/v1`，
经 8788 桥 `~/.kimi-code/proxies/agentrouter-proxy/agentrouter-proxy.py` 转发至
`agentrouter.org/v1`）链路上，`deepseek-v4-flash` 间歇 400。
用户裁决：**不许禁用渠道消错误，必须找根因修复**（初版曾误禁 ch180，已回滚恢复入池）。

## 根因（抓包 `~/.omp/logs/http-400-requests/` 回放定位，三层叠加）

1. **NewAPI relay 重序列化**：`tools[].function.parameters` 缺失 `required` 时被重写为
   显式 `null`，严格后端 400 `null is not of type 'array'`。
2. **空串 reasoning_content 回传**：assistant 消息 `reasoning_content:""` 被上游拒
   （thinking 块必须回传且非空）。占位串演进：`""`→400、`"(elided)"`→content-blocked 400、
   `"thinking"`→200（最终采用）。
3. **key 侧缺陷与冷却连坐**：keys.json key3 确定性路由严格后端（同 payload 在 key1/2
   200），属 key 侧非请求侧；且旧桥逻辑任一网关 transient 即冷却整个 key，健康 key 被
   耗尽。key0 额度尽（402 quota）。

## 修复（仅桥侧，`agentrouter-proxy.py`；备份 `.bak-20261008-400sanitize`）

- `_sanitize_chat_payload()`：`required` 非 list → 补 `[]`；deepseek 系模型 assistant
  空 `reasoning_content` → 补 `"thinking"`；命中计数写日志 `⛨ sanitized N field(s)`。
- `_KEYSIDE_400_WORDS` + `_is_retryable` 400 分支：严格后端特征 400 归 keyside，冷却换 key。
- `_classify()`/`_forward()` 统一重试分层（chat + responses 两路径共享）：
  同一 key 顺序试完全部网关；transient 只换网关不冷却 key；全部网关 transient 才冷却；
  keyside 立即冷却换 key；fatal 快速失败。MAX_ATTEMPTS=4。

## 验证

- 生产形态回放（400 抓包原样重放经修复桥）：6/6 HTTP 200。
  注意自测两个假阳性教训：合成 tool_calls 字段是 `arguments` 非 `parameters`；
  回放体上追加重复工具名会自造 400。
- A/B/C 矩阵（key × 网关）+ burst 并发多轮全 200；NewAPI 端到端
  `deepseek-v4-flash` 6 并发 6/6 200（详见 `newapi-deepseek-v4-flash-pool-2026-10-08.md`）。
- ch180 恢复入池（status=1），主 51/备 30/末 25 优先级不变。

## muyuan（ch173）两模型处置

- `qwen3.8-flash-next`：models.yml L543 已入库（10-07 批次）；10-08 OMP 实弹
  `omp -p --model zg-newapi/qwen3.8-flash-next` **200 闭环**。
- `mistral-large-4-0`：用户点名入库 → models.yml 曾加条目，**OMP 实弹确定性 503**
  报错（用户回报）。NewAPI 日志归因（`channel error (channel #173, status code: 503)`
  ×多轮）：本地路由正确选中 ch173、abilities enabled=1、本站 token 组为 default，
  错误体 `No available channel ... under group auto (distributor)` 来自 **muyuan
  自身 NewAPI 分销层**（"group auto" 是站侧分组）透传——站侧模型门死，本地无可修
  缺陷。models.yml 无"禁用条目"机制，保留必报错 → **当日摘回**，models.yml 注释留
  完整条目模板与复活条件（channel test 200 后加回）。同渠道对照组
  （qwen3.8-flash-next/mistral-code-latest）不受影响；ch173 未被 503 连坐禁用
  （status=1，abilities 全 enabled，auto_ban 未触发）。

## 回归

- `python3 -m unittest scripts.ops.test_omp_routes`：40/40 OK。
- `python3 scripts/ops/newapi-local-smoke.py`：3 FAIL 与存量基线一致
  （channel model isolation / fallback channel posture / primary opus pool posture），无新增。

## 重试耗尽状态码：4xx/502 → 503（2026-10-08 20:1x）

**症状**（用户当场报障）：OMP 弹 `400 openai_error (bad_response_status_code)`，
抓包 `~/.omp/logs/http-400-requests/1791461385299-3q6plsb657reb.json`
（20:09:45，`deepseek-v4-flash`，12 tools，158 msgs，`reasoning_effort=high`，
非本次 `max` 改动引入）。桥侧同一请求 rid=`acf4aa62` 的轨迹：

```
20:09:18 ▶ msgs=158         20:09:28 ↻ 400 keyside | ps.air-outer.com | key cooled
20:09:32 ↻ 500 transient    20:09:38 ↻ 400 keyside | agentrouter.org | key cooled
20:09:38 ↻ 全网关 transient  20:09:45 ↻ 400 keyside | ps.air-outer.com | key cooled
```

**根因**：`_forward()` 重试预算耗尽后 `raise HTTPException(status_code=last_status)`——
把**最后一个上游状态**（keyside 400）或初始默认 `502 "no keys"` 原样抛给客户端。
NewAPI 侧（`REQUIRED_OPTIONS` 钉死，见 `newapi-local-smoke.py`）：

- `AutomaticRetryStatusCodes = 408,500-503` → **4xx 不触发 failover**，ch118/ch15 备链
  形同不存在（这正是 20:09:45 用户看到裸 400 的原因）；
- `AutomaticDisableStatusCodes = 401,402,403,502` → 默认 `502 "no keys"` 落在**自动禁用集**内，
  key 池全冷却时会把主渠 ch180 连坐禁用。

**修复**（仅桥侧，`agentrouter-proxy.py`；备份 `.bak-20261008-exhaustion503`，md5 `148ee097…`）：
两处耗尽出口统一改为 `503`（含 `/v1/models` 侧原 502），detail 标 `upstream_exhausted`。
503 进重试集触发备链 failover，且不在自动禁用集。

**验证**：
- 隔离复现（桩上游恒返 keyside 400，脚本 `scripts/ops/test_agentrouter_exhaustion_status.py`）：
  改前 keyside 耗尽=`400`、空池=`502` → 改后两者均 `503`（`python3 -m unittest scripts.ops.test_agentrouter_exhaustion_status` → 2/2 OK）。
- 生产重启：`taskkill /PID 26464` → guardian 35s 内起新进程 `/health` 200 `keys=3`。
- 生产形态探针（经 NewAPI 3002，tools+stream，effort=high）→ **200 / 3.4s / 带 reasoning**。
- 改后桥日志仍见 keyside/500 抖动（13 次），但不再出现「耗尽即 4xx」：20:17:04 一次
  `500 transient → 400 keyside → 全网关 transient` 后同请求 **stream ok（20.0s）**。
- 回滚：`cp agentrouter-proxy.py.bak-20261008-exhaustion503 agentrouter-proxy.py` +
  `taskkill` 该进程，guardian ≤15s 自动拉起（`HEALTH_CHECK_INTERVAL=15`）。

## 待示下

- ~~keys.json key0（额度尽）/key3（严格后端）是否摘除~~
  **2026-10-08 20:01 处置**：逐 key × 双上游探针实锤 key0 = air-outer 403
  `user quota is not enough`（key1/2/3 同探 200；agentrouter.org 当日对四把全 503，
  站面另议）。已将 key0 移入 keys.json `keys_disabled`（带 reason/时间戳，桥只读
  `keys` 数组），备份 `keys.json.bak-20261008-200141-drop-key0`；mtime 热加载生效
  无需重启，`/health` 报 keys=3，NewAPI 端到端 `deepseek-v4-flash` 并发 4/4 200。
  key3（严格后端 keyside 400）暂留池——冷却换 key 逻辑已能确定性绕开，摘除与否待观察。
  **20:11 复发 → 2026-10-08 20:37 归因落定（keyside body 日志第一现场）**：key0 摘除后
  仍观察到 `400 keyside`（20:11:01 / 20:11:11）。桥于 20:3x 补上 keyside 分支的 body
  截断日志（≤200 字符）+ 每把 key 的 8 位 SHA1 指纹（`keyfp`）后，**当场抓获**：
  `↻ 400 keyside | ps.air-outer.com/v1 | keyfp=5620f43f | body={"error":{"message":"The
  `content[].thinking` in the thinking mode must be passed back to the API ..."}}`。
  指纹归因：池内 3 把 key 的 keyside 400 出现次数 = `{5620f43f:3, 93743b7f:2, c41b5727:1}`
  （keys[0/1/2] 全部中招）——**不是"某把坏 key"，是字段层问题**：多轮会话里 assistant
  `reasoning_content` 走 NewAPI 重序列化/桥 sanitizer 补齐后，ps.air-outer.com 严格后端
  要求 thinking 载荷以 `content[].thinking` 结构回传；agentrouter.org 宽松放行，故同
  payload 换网关即成功（20:37 实例：`keyside → 500 transient → agentrouter.org stream ok 13s`）。
  逐 key × 双网关 6/6 200 的旧探针结论作废原因：该触发器 payload 未复现 158-msg 长会话的
  thinking 载荷；日志归因（3 key 均失败）覆盖它。修复方向（供后续）：sanitizer 对
  `reasoning_content` 的补齐逻辑仅补字面占位，未规整 `content[].thinking` 结构；或对该
  严格射线挂 `content[].thinking` 适配层。
- ~~ch180 `param_override: delete reasoning_effort` 是否撤销~~
  **2026-10-08 用户裁决：保持现状（不撤）**。量化依据（同 prompt、`max_tokens=600`、每档 2 次）：
  桥直连 `max` 思维链 541/236 字符 vs `minimal` 51/75；经 NewAPI（override 删字段）两档均 50~70 字符
  ——上游认档位，是 override 把它抹平。故主链实质锁在上游默认档（官方口径 `high`），且关思考的旁路
  也不通（`thinking:{type:disabled}`/`enable_thinking:false` 实测均无效）。详见
  `newapi-deepseek-v4-flash-pool-2026-10-08.md` 思考强度一节。
- ~~桥 sanitizer/重试改动是否镜像一份到 `scripts/ops/`（现仅生产文件；耗尽状态码已有
  仓库侧不变式测试 `test_agentrouter_exhaustion_status.py`）。~~
  **2026-10-08 20:3x 执行（用户授权）**：桥已入仓托管——`scripts/ops/agentrouter-proxy.py`
  为可审阅真源（含 keyside body 截断日志 + key 指纹），部署经
  `scripts/ops/deploy_agentrouter_proxy.py`（`--check` 漂移门禁 / `--apply` 备份+原子替换+
  guardian 重启+健康+生产探针 / `--restart` / `--rollback`，manifest 落
  `~/.kimi-code/proxies/agentrouter-proxy/backups/`），`test_mirror_sync.py` 增
  bridge 逐字节一致门禁 + keys.json 永不入仓断言（5/5 OK）。当前 deployed
  `d589b043`（backup `.bak-20261008-203605`，pre-image `b5c81de7`），
  `--check` 输出 `in sync`。
