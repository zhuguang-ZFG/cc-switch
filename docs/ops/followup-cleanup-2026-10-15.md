# 收尾清理（2026-10-15 晚）：OMP 仓补提交 / Atria 定案 / ch178 zen 桥回加

承接同日 `dead-model-triage-2026-10-15.md`。四项收尾：① `~/.omp/agent`
本地仓 6 天未提交；③ alignment 唯一 orphan `Atria-Dawn-Preview`；④ ch178
zen-free-bridge 在 CLI 恢复后回加被摘除的免费腿；⑦ 门禁 quarantine 文案
陈旧与重复键。

## ① OMP 本地仓补提交（commit `f7a0e75`）

- 事实：`~/.omp/agent` 最后提交停在 `4ad3f04`（2026-10-09 21:46），此后
  6 天改动全在工作区未提交：`config.yml`/`models.yml`（360 行 diff）/
  `mcp.json`/`task-verification-policy.json`/`extensions/` + 3 个新
  `agents/*.md` 未跟踪。README 约定"改后即 commit，取代 .bak"——期间无任何
  本地回滚点。
- 提交内容：角色重指后的 `config.yml`、`models.yml`（+15/−26 id vs 10-09）、
  `mcp.json`（+jev/+rea server）、`task-verification-policy.json`
  （+monidianlu root）、compaction 扩展 r12、3 个 agent 定义；
  `.gitignore` + `*.backup`（`mcp.json.rea.backup` 类一次性备份）。
- 顺手修：compaction 候选表里的 `zg-newapi/qwen3.8-flash-next` 已在本轮
  从 models.yml 移除（上游 503 退役），从候选删除并留注记。
- 本地仓无 remote（models.yml 含明文 key），仅本地版本化。

## ③ Atria-Dawn-Preview（ch139）→ quarantine 定案（不回开）

- admin test `GET /api/channel/test/139?model=Atria-Dawn-Preview` →
  HTTP 200、`success=false`、上游 **429 `Token quota exhausted`**
  （`atria_api_error`/`insufficient_quota`）。
- `guardian.log` 10-14 19:54–21:24 连续 12 轮恢复探测全 429 后预算用尽
  （"exhausted the recovery probe budget (12 failed cycles, budget 12)"）。
- 判定：1 亿 token 免费额度耗尽，属诚实失败；alignment 表加 quarantine
  条目（re-enable 仅在上游额度重置后）。门禁由此归零。

## ④ ch178 zen-free-bridge 回加（新脚本 `sync_zen_free_bridge_models_20261015.py`）

背景：10-11..13 桥 CLI 挂死（150–180s 超时、grandchild 泄漏）期间把 6 个
id 从 ch178 摘除；当日桥已恢复（nemotron-3.5/longcat/step-5/muse-1.2 均有
真实流量）。

live catalog（`opencode models`，10 个 `opencode/*`）：
big-pickle, cat-jam-free, deepseek-v4.1-flash-free, longcat-2.5-preview-free,
mimo-v2.6-flash-free, muse-spark-1.3-contributor-free, nemotron-3-ultra-free,
nemotron-3.5-lightning-free, space-bunny-free, step-5-preview-free。

apply 终态（备份 `new-api-before-zen-free-bridge-recover-20261015-173645.db`，
66,478,080 B、integrity=ok）：

| 项 | 结果 |
|---|---|
| models | 5 → **11**（keep 5 + re-add 6：big-pickle、cat-jam-free、deepseek-v4.1-flash-free、mimo-v2.6-flash-free、muse-spark-1.3-contributor-free、space-bunny-free） |
| ModelRatio | +6 × 0（无删；`removed=[]`） |
| readback | models/abilities(11 enabled)/ratios 全一致 |
| admin test | big-pickle → HTTP 200 `success=True` |
| 探活 | 11 候选全 200（含 503-busy 后重试恢复的 nemotron-3-ultra） |

**教训（已固化进脚本）**：

- 单发 502 是瞬态：nemotron-3.5 / longcat 17:31 探活 502，20 分钟后同桥
  再探 200；
- 503 `zen bridge busy` 是单飞锁与真实网关流量竞争，不是模型故障；
- 24h 内有过真实 completion（`completion_tokens>0`；免费模型 `quota=0`
  不能当判据）的模型一律保留；
- 因此同步默认 **additive**（`--allow-drop` 才摘除），摘除需干净窗口证据。

## ⑦ 门禁 quarantine 文案刷新（`verify_omp_newapi_alignment.py`）

- 删重复键 `gemini-3.8-flash`（Python dict 后键遮蔽新证据）→ 合并一条；
- `glm-5.3` 更新：plan/designer/task 已迁 k3、advisor 迁 glm-5.3-flash，
  OMP 无消费方；
- ch178 复活 4 条删除（big-pickle / space-bunny-free / mimo-v2.6-flash-free /
  muse-spark-1.3-contributor-free）；
- `ling-3.1-flash-free` / `ling-3.0-flash-fin-free` → 上游目录已退役
  （无回加路径）；
- `muse-spark-1.2-contributor` 文案修正（free twin 已在 ch178 服务）；
- 新增 Atria 条目（见 ③）。

## 门禁（终态实测）

- `verify_omp_newapi_alignment.py` → **CLEAN**：68 ids / **0 orphan** /
  20 quarantined（改动前 1 orphan + 23 quarantined）。
- `test_omp_routes` → **40/40 OK**（2.6s，静态门禁）。
- `newapi-local-smoke`（17:38 手动轮）→ FAIL 集合与 16:12 基线**逐项一致**：
  automatic channel recovery / channels / ai.168661 posture / primary opus
  pool posture / backup opus pool posture / pool capacity claude-opus-4-8 /
  unregistered model ratios / pool answer rate。ch178 变更未引入新失败类。
  同轮 pool answer rate 新增第二项：`glm-5.3-flash req=271 ok=131 rate=48.3%`
  （阈值 50%，贴线观察项）。

## 新发现（P0 已处置；其余待裁决/复检）

1. **slow 角色满路死（P0）**：`zg-newapi-anthropic/claude-opus-4-8:xhigh`
   全天 0%——perf_metrics：10-15 16:00 `req=308 ok=0`、14:00 11/0、
   13:00 54/0、11:00 33/0、08:00 44/0、10-14 22:00 158/0、18:00 98/0；
   17:38 smoke 轮 `req=373 ok=0`。ch72（anyrouter，唯一 enabled 腿）
   admin test → **429 Service Unavailable**；ch94/95 justwoker 两腿
   status=2；ch3（baibei）admin test 90s 超时（未定论）。
   opus-5 同样 0%（13:00 req=20 ok=0）。→ **已处置，见下节「slow 角色重指
   k3」**。
2. **ModelRatio 缺口 6 名**（24h 实测，隐含 37.5）：qwen3.8-flash
   x162/740M（ch186 tierflow）、qwen3-8-27b x57、glm-4.6v x33（vision 角色）、
   gpt-6-astra x31、kimi-k3-oc x32（ch202 ctyun promo）、jina-embeddings-v3
   x29（embedding 角色）。库内无 embedding 先例；同族参照 glm-4.6=0.5 /
   gpt-5.6-sol=0.5 / kimi-k3=2 / glm-4.6v-flash=0。**等用户给值**。
3. **muse-spark-1.2-contributor-free 观察**：17:31 起 CLI 报
   `UnknownError err_26c79a89`（fast fail，非挂死），且已不在 `opencode models`
   目录；16:36 还有真实 ct=3。按 24h 宽限策略保留，明日复检再定摘除。
   **18:15 复核（第 3 次连续失败）**：17:31 探活 502 → 17:44 直连 CLI
   `UnknownError` → 18:15 探活仍 fast-fail；同一时刻同桥的
   muse-spark-1.3-contributor-free 探活 200 → 排除桥故障，判定 1.2 为
   上游退役。影响可控：config.yml 有 model-keyed 兜底
   （`muse-spark-1.2-contributor-free` → `deepseek-v4-flash`），用户侧只多
   一跳。明日宽限到期后按 `--allow-drop` 摘除（或按 1.3 同槽处理）+
   门禁加 quarantine 条目。

## slow 角色重指 k3（P0 处置，需 OMP 重启生效）

证据（见上节新发现 1）：`claude-opus-4-8` 全天 0% 成功率（perf_metrics
逐桶 0；17:38 smoke 轮 req=373 ok=0），唯一 enabled 腿 ch72（anyrouter）
admin test → 429 Service Unavailable；ch94/95 status=2；ch3（baibei）
admin test 90s 超时未定论；opus-5 同为 0%。

变更（`~/.omp/agent/config.yml`）：

- `modelRoles.slow`：`zg-newapi-anthropic/claude-opus-4-8:xhigh` →
  **`zg-newapi/k3:max`**；
- `fallbackChains.slow`：`[zg-newapi/k3:max]` → **`[zg-newapi/qwen3.8-max]`**
  ——不去 DeepSeek：静态门禁 `test_reasoning_role_chains_exclude_deepseek`
  禁止推理角色（plan/designer/slow）链可达 DeepSeek；同时消除「链内重复
  主选」（门禁另有硬校验）。qwen3.8-max 活体腿 ch118（10-15 实况）。

行为增量：原配置主选必失败、经链落 k3（且 `fallbackRevertPolicy:
cooldown-expiry` 冷却到点还会反复回探死主选）——重指后同一 k3 输送路径，
省掉每次调用前的废试与回探抖动；无新增渠道负载（k3 本就在承接）。

门禁：`test_omp_routes` **40/40 OK**（改动后实测）。**待 OMP 重启生效**；
回滚=反向改回两处（或 `git -C ~/.omp/agent revert <本 commit>`）。

## 回滚

```bash
# ch178：恢复 5-model 串（或整库）
cp ~/.new-api-local/backups/new-api-before-zen-free-bridge-recover-20261015-173645.db \
   ~/.new-api-local/new-api.db   # NewAPI 停机时
# 门禁/文档：git revert <本 commit>
# OMP 仓：git -C ~/.omp/agent revert f7a0e75
```

## task_verify 接线（ops-gates，2026-10-15 追加）

本轮两个改进文件此前不在任何 check 的 paths 内（task_verify 报
`unverified, checks: []`）。现补：

- 测试：`scripts/ops/test_ops_gates.py`（hermetic，11 例，无网络/DB）——
  QUARANTINE 重复键 AST 守卫（对 fd222080 旧版实证检出被遮蔽的
  `gemini-3.8-flash`，对 HEAD 通过）、`merge_ratio` 语义、`compute_target`
  additive/drop/宽限策略、桥探针 200+正文判定与重试；
- 同步脚本抽出纯函数 `compute_target`（行为不变，dry-run 复核 plan 一致）；
- 策略双副本：repo 源 `scripts/ops/task-verification-policy.json`
  （`@repository`/`@python` 占位，`deploy-omp-verification.py` 展开部署）
  与生效副本 `~/.omp/agent/task-verification-policy.json` 均加 `ops-gates`；
- **语义**：改任一副本后 task_verify 返回
  `verification-policy-changed-review-required`，须人工 review 后下一轮
  才建立新基线——期间改动保持 unverified（本轮即如此）。
