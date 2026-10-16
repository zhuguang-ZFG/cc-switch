# opencode glm-5.3-flash 重新免费 → 接入 ch178 并注册 OMP（2026-10-16）

用户报：opencode 又把 **glm-5.3-flash 免费**了。核实与接线如下。

## 上游证据

- `opencode models`（真 CLI 目录）新增 `opencode/glm-5.3-flash-free`（同批还有
  `ll-alpha-free`、`nemotron-3.5-super-vl-free`、`cat-jam-free`、`deepseek-v4.1-flash-free`
  等免费 id）；
- 桥侧实探（dry-run，探针缓存 30 分钟）：**`glm-5.3-flash-free` LIVE 200（14.6s）**、
  `ll-alpha-free` 200（6.5s）、`nemotron-3.5-super-vl-free` 200（6.9s）；
- 同步计划 `drop=[]`（现有 11 只全有 24h 内真实流量），无 429/无 dead。

## 变更

**ch178（zen-free-bridge）11 → 14 只**，用 house 脚本
`scripts/ops/sync_zen_free_bridge_models_20261015.py --apply`（快照 → PUT models →
ModelRatio=0 → readback → admin test，任一步失败自动回滚）：

- 新增：`glm-5.3-flash-free`、`ll-alpha-free`、`nemotron-3.5-super-vl-free`；
- 快照 `new-api-before-zen-free-bridge-recover-20261016-151430.db`（integrity ok）；
- readback：models/abilities/ratios 三项一致；admin test `glm-5.3-flash-free` HTTP 200。

**OMP `models.yml` 新增条目**（ch178 免费族，紧随 cat-jam-free）：

```yaml
- id: glm-5.3-flash-free
  compactionModel: zg-newapi/deepseek-v4-flash
  reasoning: false        # 实测：reasoning_effort/thinking 传参后仍无 reasoning 输出
  supportsTools: false    # 实测：tools 传参 200 但无 tool_calls（散文作答）
  contextWindow: 131072   # 实测 ~125.5k-token 提示 200 且中段标记读回 ✓
  maxTokens: 65536        # 实测 32768/65536 均被接受（生成上限未逐量测）
```

（与付费 `glm-5.3-flash` 条目并存：付费名走 ch208/ch118/ch183/ch173 四腿池，免费名走
ch178 单腿。）

## 验证（经网关，即 OMP 实际路径）

| 探针 | 结果 |
|---|---|
| 小请求（max_tokens=64） | 200 / 8.9s / `content='OK'`，无 reasoning |
| tools（get_weather, tool_choice=auto） | 200 / 27.8s / **无 tool_calls**，散文作答 |
| `reasoning_effort: high`、`thinking: enabled` | 200，仍无 reasoning 输出 |
| 上下文标记（~125,568 token） | **200 / 15.8s / 标记 `ZEN-GLM-MARKER-7741` 读回 ✓** |
| max_tokens 32768 / 65536 | 均 200（接受） |
| `verify_omp_newapi_alignment.py` | **CLEAN** |
| `test_omp_routes` | **40/40 OK** |
| 冒烟 | **FAIL 仍 5 类、无新增**（3 个新 id 带 ratio 0） |

## 「zen 做主力」（用户指令，同日 15:25）

指令原文：「zen做主力」——即让 zen 免费腿成为该 id 的**主腿**（回答上文待办第 1 条）。

**做法**（`scripts/ops/make_zen_primary_glm53flash_20261016.py`，dry-run 默认、失败自动回滚）：

- ch178 models += `glm-5.3-flash`，并加 `model_mapping {"glm-5.3-flash": "glm-5.3-flash-free"}`
  （桥只认免费 id，付费名经映射转发）；
- ch178 priority **0 → 30**（高于付费池最高腿 ch118 p25）→ 该名默认落 zen；
- 波及面已核：ch178 的 15 个 id 中仅 `longcat-2.5-preview-free` 另有他渠活腿
  （ch133 p1），抬高后同样变为 zen 优先——与「主力」意图一致。

**证据**（全部实测）：

| 检查 | 结果 |
|---|---|
| readback | models 15、mapping 含新键、priority=30、**key 哈希未变**、**channel_info 未变**、ability (1 行, enabled=1) |
| admin test `ch178/glm-5.3-flash` | HTTP 200 success=True |
| 网关实弹 `glm-5.3-flash` | 200 `content='OK'` |
| **日志归因** | 最新请求 `channel_id=178`（= 主腿生效的决定性证据） |
| 门禁 | alignment CLEAN、`test_omp_routes` OK、冒烟仍 5 类（无新增） |
| 快照 | `newapi-before-zen-primary-glm53flash-20261016-152542.db` |

**house 事实（本次顺带确认）**：`GET /api/channel/` 返回的 `key` 是**空串**（sha=e3b0c442…），
且 fork 对 PUT 传入空 key **保留原值**——这正是同步脚本今天没打坏 ch178 的原因。本脚本
仍按规约**从 SSOT 显式 hydrate key** 并读回哈希校验，避免依赖该隐式行为。

**能力注意点（已写入 OMP 条目注释）**：免费主腿**不支持 tools**（tools 传参 200 但无
tool_calls）、**无 reasoning 输出**（`reasoning_effort`/`thinking` 传参被忽略，仍正常作答）、
免费日额度用尽时桥返回 **429** —— 按 house 既有设计（smoke 注原文：**08-11 起 429 被
刻意移出重试表**，"429 透传交客户端退避 + 回退链；重试仅覆盖瞬时类状态码"），
**NewAPI 不会因 429 换腿**，而是由 **OMP 的 `fallbackChains` 降级到下一模型**（smol 链：
glm-5.3-flash → deepseek-v4-flash → mercury-2 → muse-spark-1.2-contributor-free）；
付费腿（ch118/ch173/ch208）只在 408/500-503 类可重试错误上按优先级接替。付费腿的
`reasoning: true` 声明保持不变（本 id 的 reasoning/thinking 声明按付费腿能力写）。

**顺带解决的历史问题**：`pool-error-triage-2026-10-13.md` 当时记录「glm-5.3-flash 有效
容量=2 条 5h 窗口福利腿，OMP tiny/smol 兜底流量全压这里」并留了「找非窗口型 glm-flash 腿」
的待办——zen 免费腿正是**非窗口型**，本次改动即该项的落地。

**OMP 侧**：`glm-5.3-flash` 条目 name 串更正为「zen ch178 p30 主档; ch118 p25 / ch173 p20 /
ch208 p5 备档」（原文案错误地写着「4腿池 ch208 p30 / ch118+ch183 p25」——ch183 早已
enabled=0、ch208 实为 p5）；新增注释记录上述路由与能力注意点。**OMP 重启后生效。**

## 备注与待办

- **需 OMP 重启**才会读到 models.yml 新条目（渠道侧即时生效）；
- `ll-alpha-free`、`nemotron-3.5-super-vl-free` 已进 ch178**但未注册 OMP**（可用但未挂；
  要挂需各自实测能力后加条目，属后续按需事项）；
- 免费额度用尽时桥统一返回 **429**（house 语义：420/429 属配额、非故障，勿据此判死）；
- 该入口与付费 `glm-5.3-flash` 是不同 id：若想让既有付费名的请求优先走免费腿，需要
  在渠道里做 `model_mapping` 别名或改 OMP 路由——**属用户裁决项，本次未动**。

## 回滚

- ch178：`cp` 快照 `new-api-before-zen-free-bridge-recover-20261016-151430.db`（停机），
  或按同步脚本的 PUT 步骤把 models 串改回 11 只 + 删三条 ModelRatio=0；
- OMP：删掉 `models.yml` 的 `glm-5.3-flash-free` 条目（OMP 仓版本化，可 git revert）。
