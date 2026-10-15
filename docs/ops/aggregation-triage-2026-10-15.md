# 聚合渠道报错定位与修复（2026-10-15 晚）

用户报「好多模型聚合在一起会报错」。方法：对 16 条 enabled 多模型渠道做
**84 对 (渠道×模型) 探活矩阵**（每对 1 次 + 失败复测 1 次）→ **63 OK / 21 FAIL**；
按错因分类后裁目录、修链。

## 错误来源定性（先分清本地 vs 上游）

- `No available channel for model {{.Model}} under group {{.Group}} (distributor)`
  → **本地 fork 自己发的**（`new-api.exe` 内含该模板串）＝本地无渠道路由；
- `Not enough quota to pre-consume ...` → **上游透传**（二进制中无此串），
  即某个 NewAPI 类转售网关的账号余额不足以预扣。

## 21 个 FAIL 的分类与处置

| 类 | 配对 | 证据 | 处置 |
|---|---|---|---|
| **上游不提供该模型**（目录漂移） | ch15 `sensenova-u1.5-lite`；ch188/189/190/191 `gpt-5.5` | 404 `model is not found`；503 `auth_unavailable: ... gpt-5.5 is not supported when using Codex`（4 渠道同错） | **从目录裁剪**（6 渠道 PUT，写前快照 `new-api-before-catalog-trim-20261015-193212.db`，readback + abilities 归零核验） |
| **渠道活、该模型死** | ch118 `qwen3.8-max`(502)、`grok-4.5`(500)、`qwen3.7-max`(502→429 risk-control)、`claude-sonnet-5`(402 balance) | 同渠道 `glm-5.3-flash`/`deepseek-v4-flash` 均 200；前三者净窗复探第二次仍 500 | 同上裁剪（ch118 → 2 模型） |
| **整渠死（既知）** | ch3 ×4（100s 超时）、ch72 ×5（429 窗口）、ch204 ×3（本地 workbuddy 桥 down） | 与既有 runbook 一致 | 不裁（ch3/ch72 已有恢复机制；ch204 桥恢复即回） |

**修复效果**：这些名字在被路由时不再打到"不可能成功"的渠道（快速本地失败或
路由到其他腿），错误从"上游随机报错"变为可归因。

## OMP 侧联动

- **4 个新 orphan 入册**（alignment CLEAN 恢复）：`qwen3.8-max`、`qwen3.7-max`、
  `grok-4.5`、`gpt-5.5`（各自附再启用条件：seeseed 恢复 / jojatoken 支持 /
  midjok key 复活）。
- **死链修复**（`config.yml`）：
  - `vision`：`[qwen3.8-max, step-3.7-flash]`（两跳全死）→
    **`[zg-newapi-anthropic/intern-s2-preview, zg-newapi/dots-3-note-prev]`**；
  - `plan` 第二跳：`qwen3.7-max`（死）→ `zg-newapi/glm-5.3`；
  - `slow`：本晚早先挂的 `qwen3.8-max`（正好被裁）→
    `zg-newapi-anthropic/intern-s2-preview`；
- **compaction 候选**（extension）：`gpt-5.5` → `glm-5.3-flash`；删
  `glm-5.2`、`step-5-preview`（全暗）。

**如实记录一处自己的错误**：首次把 `intern-s2-preview` 写成
`zg-newapi/intern-s2-preview`，被静态门禁
`test_live_fallback_chains_have_no_hard_violations` 当场拦下（该 id 注册在
**`zg-newapi-anthropic`** provider）；改为 anthropic 选择器后 40/40。

## 附带发现

- `muse-spark-1.2-contributor-free` **已自行恢复**：矩阵探活 ch178 9.1s 200
  （17:31–18:15 的连续 fast-fail 结束）→ 早前"明日摘除"计划取消，留库观察。
- 全部改动的生效条件：`config.yml`/extension **下次 OMP 启动**；渠道目录裁剪
  即时生效。

## 门禁（终态实测）

- `test_omp_routes` **40/40 OK**；`verify_omp_newapi_alignment` **CLEAN**；
  冒烟仍 **5 类**（无新增）；`test_ops_gates` **11/11**。

## 回滚

```bash
# 目录裁剪：cp ~/.new-api-local/backups/new-api-before-catalog-trim-20261015-193212.db <db>（停机）
# 链/候选：git -C ~/.omp/agent revert <本 commit>
```
