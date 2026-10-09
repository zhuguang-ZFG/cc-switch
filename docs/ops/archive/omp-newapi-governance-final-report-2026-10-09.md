# OMP + NewAPI 治理最终报告 — 2026-10-09

## 执行摘要

本次治理解决了 OMP 配置中的关键失效问题，确保所有角色和 fallback 链指向可用的 NewAPI 渠道。

**治理结果：** ✅ 全部完成，无遗留问题

---

## 问题发现与修复

### 🔴 严重问题（已修复）

| # | 问题 | 影响 | 修复方案 |
|---|------|------|----------|
| 1 | k3 无活渠道 | plan/designer/task 角色失效 | 角色换指 `glm-5.3:max`（5 活渠道） |
| 2 | 8 个 OMP 模型悬空 | models.yml 引用死模型 | 从 models.yml 移除 |
| 3 | 3 个 fallback chain 悬空 | slow/advisor 引用已删模型 | 换指 glm-5.3/qwen3.7-max |

### 详细修复清单

#### 1. 角色换指（config.yml modelRoles）
```
plan:     zg-newapi/k3:max      → zg-newapi/glm-5.3:max
designer: zg-newapi/k3:max      → zg-newapi/glm-5.3:max
task:     zg-newapi/k3:max      → zg-newapi/glm-5.3:max
```

#### 2. Fallback chains 修复（config.yml retry.fallbackChains）
```
slow:     zg-newapi/k3          → zg-newapi/glm-5.3:max
advisor:  zg-newapi/k3:max      → zg-newapi/glm-5.3:max
          zg-newapi/gpt-6-luna:max → zg-newapi/qwen3.7-max
```

#### 3. models.yml 清理（8 条目移除）
- `k3` — 所有渠道 disabled
- `glm-5.3-flash` — 所有渠道 disabled
- `gpt-6-sol` — 所有渠道 disabled
- `grok-4.6` — 所有渠道 disabled
- `qwen3.8-max` — 所有渠道 disabled
- `agnes-2.5-pro` — abilities 不存在
- `gpt-6-luna` — abilities 不存在
- `ling-3.3-flash-free` — abilities 不存在

---

## 验证结果

### Smoke Test
```
summary: ALL OK
- 77 channels (33 enabled, 42 disabled, 2 other)
- 0 posture violations
- 0 model isolation violations
- claude-opus-5 pool: 3 enabled channels (min=2) ✓
```

### OMP 角色验证
```
✓ slow:      zg-newapi-anthropic/claude-opus-5:xhigh (3 live)
✓ plan:      zg-newapi/glm-5.3:max (5 live)
✓ commit:    zg-newapi/u2-flash (1 live)
✓ tiny:      zg-newapi/agnes-2.5-flash (2 live)
✓ vision:    zg-newapi/dots-3-note-prev (1 live)
✓ smol:      zg-newapi/u2-flash (1 live)
✓ designer:  zg-newapi/glm-5.3:max (5 live)
✓ task:      zg-newapi/glm-5.3:max (5 live)
✓ advisor:   zg-newapi/glm-5.3 (5 live)
✓ default:   zg-newapi/qwen3.8-flash-next:high (1 live)

10/10 roles → live channels ✓
```

### Fallback Chains 验证
```
23/23 references → valid models ✓
```

---

## 统计对比

| 指标 | 治理前 | 治理后 | 变化 |
|------|--------|--------|------|
| OMP models (zg-newapi) | 65 | 57 | -8 |
| OMP 悬空条目 | 8 | 0 | -8 |
| 失效角色 | 3 | 0 | -3 |
| 悬空 fallback 引用 | 3 | 0 | -3 |
| NewAPI 死模型 | 33 | 33 | 无变化* |

*注：33 个死模型保留在 abilities 表中（enabled=0），会在渠道恢复时自动复活。

---

## 备份位置

```
~/.omp/agent/config.yml.bak-before-governance-20261009
~/.omp/agent/models.yml.bak-before-governance-20261009
```

---

## 提交记录

| Commit | 描述 |
|--------|------|
| `4701d7f4` | docs(ops): 补充 fallback chains 修复文档 |
| `f838c562` | docs(ops): OMP + NewAPI 治理 runbook |
| `798c529b` | docs: fix channel count in release notes |
| `b996356a` | ops: add posture contracts for 8 new channels + release notes |
| `f8a192a7` | docs: archive all runbooks to docs/ops/archive/ |
| `d85deaf5` | ops: NewAPI 渠道批量接入 + smoke auth 修复 + 死渠道清理 + OMP 瘦身 |

---

## 后续建议

1. **ch33 kimi-official-k3 恢复评估** — 该渠道被 Guardian 禁用，如 Kimi 上游恢复可考虑重新启用
2. **42 个 disabled 渠道定期审查** — 部分渠道可能已永久失效，可考虑从 DB 删除
3. **Guardian 与 smoke posture contracts 同步** — 当前两者独立运作，可考虑让 Guardian 读取 posture contracts

## 后续修复（同日）

### 思维链强度配置补全

发现 35 个 reasoning 模型缺少 `thinking` 配置块，3 个模型缺少 `max` 强度。

**修复内容：**
- 添加 `thinking` 块到 35 个模型
- 补充 `max` 强度到 3 个模型（mimo-v2.6-flash-free, ling-3.1-flash-free, agentrouter/glm-5.3）
- default 角色升级为 `:max` 强度

**修复后统计：**
- 66 个模型中 47 个有 thinking 块
- 46 个支持 max 强度
- 所有 reasoning 模型均有 thinking 配置 ✓

---

**治理完成时间：** 2026-10-09 13:34  
**思维链修复时间：** 2026-10-09 14:15  
**执行人：** AI Agent  
**验证状态：** ✅ ALL OK
