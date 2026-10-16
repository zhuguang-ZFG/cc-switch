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
