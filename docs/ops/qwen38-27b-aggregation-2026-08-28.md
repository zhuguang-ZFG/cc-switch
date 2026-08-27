# Qwen 3.8 27B 二源聚合（2026-08-28）

**Status:** Active（OMP default 模型 `zg-newapi/qwen3-8-27b` 由单源 ch88 升级为 ch88+ch112 二源 1:1）
**Scope:** 本地 NewAPI（新建渠道 ch112 + abilities 机制）、ops 脚本、聚合快照文档；OMP 侧零改动

## 背景

longcat→qwen3-8-27b 替换（2026-08-28，`ca8e99de`）后，OMP default 模型（兼
commit/smol 角色、translator、compaction 第 4 候选）只有 ch88
`runinfra-qwen3-8-27b`（`https://api.runinfra.ai`，单 key）一个源。用户选择
"找同 model id 的第二家 relay"（参照 deepseek 池多源混跑模式）做真上游级冗余。

## 供应商扫描（2026-08-28，既有 key 优先）

逐一直连探测本地 NewAPI 全部 22 个 status=1 渠道的 `/v1/models`（key 不落盘、
不打印），另查 OpenRouter 与 HuggingFace 公开端点：

- **ch110 `yjs-free`（`https://api.yjs.im`，既有 key）上游列 `qwen-3.8-27b`**
  ——与 runinfra 的 `qwen3-8-27b` 同一开源模型（HF `Qwen/Qwen3.8-27B`，
  Qwen 3.8 27B 开放权重）的中转命名差异。功能探测：chat completion 返回
  `model=qwen-3.8-27b`（推理模型，`max_tokens` 小时正文在 `message.reasoning`
  而非 `message.content`——首次探测因此误判 None）。**选中：零新账户、零新 key。**
- OpenRouter 公开端点有 `qwen/qwen3.8-27b`（需新账户+付费，未用）。
- HuggingFace 有官方仓 `Qwen/Qwen3.8-27B`（HF 推理免费档限流，未用）。
- 其余既有渠道（sensenova/kimi/gorouter/agnes/dots/ooioo/sotamodel/whyyin 等）
  均无该模型；runinfra 自身仅 ch88 一家。

## v1 失败与机制发现（重要教训）

初版方案"在 ch110 增模型 + 直接 SQL 写 abilities 50/50"应用后 verify 失败
（ch88 行仍是 0/1），自动回滚生效。根因有二：

1. **abilities 表是 channel 行的派生物**：行存在性镜像 `channels.models`，
   `(priority, weight)` 镜像渠道级 `priority`/`weight`；sync goroutine 在渠道
   API 事件后重新派生（直接 SQL 把 ch88 行改 50/50，数秒内被还原为渠道字段
   0/1；ch110 回滚增模后，其孤儿 abilities 行亦被 goroutine 清除）。
   **per-model 权重只能靠渠道级字段控制。**
2. **Python sqlite3 legacy 事务不显式 `commit()`，DML 在 `close()` 时静默回滚**
   ——verify 读到的"未变"其实是自己没提交。

推论：ch110 挂 19 个模型，抬它的渠道级 weight 会扰动其参与的全部池
（deepseek-v4-flash/glm-5.2/k3/muse 等），不可为单一模型调权。

## 终版设计（v2，已应用）

第二源建**专用单模型渠道**（与 ch88 自身同模式，本部署既有惯例）：

- 新建 ch112 `yjs-qwen3-8-27b`：`base_url=https://api.yjs.im`，key 复制自
  ch110（同 host 同 key，无新增用量语义），`type=1`，`models=qwen3-8-27b`，
  `model_mapping={"qwen3-8-27b":"qwen-3.8-27b"}`，`group=default`，
  `priority=0, weight=1, auto_ban=1, test_model=qwen3-8-27b`。
- **ch88 零改动**：单模型渠道、渠道字段无纠缠，现有 0/1 与新渠道 0/1 天然
  同优先级 1:1 权重混跑。
- ch110 零改动：其 19 模型的既有路由完全不受影响。
- OMP 侧零改动：model id 不变，NewAPI 权重轮询 + auto_ban（双渠道均 auto_ban=1）
  容灾；ch112 被 auto-ban 时流量全落 ch88，额度回血由 AutomaticEnable 拉回。
- 无 ModelRatio 改动：`qwen3-8-27b` 本就不在 193 条 ModelRatio 里，ch88 自
  2026-08-27 起按默认 1.0 计费运行正常。

## param_override 核查（本会话正跑在 ch88 上，必须排除自伤）

ch88 带 `param_override={"operations":[{"path":"prompt_cache_key","mode":"delete"}]}`
（runinfra 拒未知参数，故剥离）。ch112 初建未带 override，而 OMP 真实请求体
含 `prompt_cache_key`/`stream_options`/`enable_thinking`（ch89 的先例是剥
`enable_thinking`）。**直连 api.yjs.im 逐参数矩阵实测**（stream=true，四个变体：
仅 prompt_cache_key / 仅 stream_options / 仅 enable_thinking / 三者全带）：
**全部 HTTP 200 正常 SSE**。且带 `prompt_cache_key` 时 yjs 响应签名变为
`model:"Qwen/Qwen3.8-27B"` + `chatcmpl-` id（不带时为 `qwen-3.8-27b` +
`logfare-` id）——yjs 侧按该参数走缓存感知后端，**剥掉反而损失缓存局部性**。
结论：ch112 不加 param_override。再加一层保险：经网关以**完整 OMP 形请求体**
（stream + stream_options + tools + tool_choice + reasoning_effort +
prompt_cache_key + enable_thinking）打 12 发，**12/12 成功**。

## 应用与验证

- 脚本 `scripts/ops/add_qwen38_27b_pool.py`（默认 dry-run，`--apply` 生效；
  POST 用三坑 wrapper `{"mode":"single","channel":{...}}`、不带 `status` 字段；
  失败自动 DELETE 新渠道 + 清 abilities 行）。
- 快照备份：`<NewAPI DB 目录>/backups/new-api-before-qwen38-27b-pool-20260828-011003.db`
  （128,761,856 字节，integrity ok）。
- 应用后 readback：ch112 渠道字段与 mapping 正确；abilities 行由 sync goroutine
  自动落为 `('default', 1, 0, 1)`（无需 SQL）；ch88 行保持 `('default', 1, 0, 1)`。
- **功能验证（无亲和键，纯权重分流）**：经网关 12 发最小形 `qwen3-8-27b` 请求，
  `logs` 表 channel 归属 **ch88:4 / ch112:7**——二源均命中，1:1 权重成立。
- **功能验证（全形 + 亲和）**：完整 OMP 形 12 发 12/12 成功，归属
  **ch112:12**——非权重问题，是 "qwen trace" 渠道亲和规则（`key_path=
  prompt_cache_key`）把同一 cache key 粘性钉在首个命中渠道（本会话的 key 已被
  钉在 ch88，故本会话流量不受池变更影响；新 session 的 key 才按权重分流）。
- ch112 上游身份验证：直连 `GET /v1/models` 含 `qwen-3.8-27b`；completion
  `model=qwen-3.8-27b`。

## 故障模式与恢复

- **ch112 上游挂/限流**：auto_ban 摘除 → 被亲和钉在 ch112 的 session 重路由
  到存活渠道（ch88），未钉的流量全落 ch88（回到聚合前状态，不劣化）；恢复后
  自动拉回。
- **ch88 挂**：同理，流量落 ch112（yjs 免费档，容量未压测；若双源同时不可用，
  default 模型短时不可用——与聚合前同级，非新增风险）。
- **回滚**：DELETE ch112（goroutine 随之清 abilities 行）；或整库快照
  `new-api-before-qwen38-27b-pool-20260828-011003.db` 还原。
- **调权重**：改渠道级字段（PUT 渠道，排除 `status`）；勿直接 SQL 写
  abilities 的 priority/weight（会被 goroutine 还原）。

## 文档同步

- `docs/patches/newapi-aggregation-pools-2026-08-01.md`：单源表行更新为
  二源 1:1；新增"2026-08-28 聚合落地"注（含机制教训 + param_override 核查）；
  验证记录加一条。
- 本 runbook；`.trellis/spec/ops/zg-gateway-claude-code.md` 无渠道级描述，未动。
