# OMP + NewAPI 治理 — 2026-10-09

## 问题诊断

### 🔴 严重问题（已修复）
1. **k3 无活渠道** — plan/designer/task 角色失效
   - 根因：ch33 kimi-official-k3 被 Guardian 禁用（auto_ban=1），所有 6 个 k3 渠道均 disabled
   - 修复：roles 换指 `glm-5.3:max`（5 个活渠道）

2. **8 个 OMP 模型悬空** — models.yml 引用无活渠道的模型
   - `k3`, `glm-5.3-flash`, `gpt-6-sol`, `grok-4.6`, `qwen3.8-max`（abilities 存在但全禁用）
   - `agnes-2.5-pro`, `gpt-6-luna`, `ling-3.3-flash-free`（abilities 不存在）
   - 修复：从 models.yml 移除这 8 个条目

### 🟡 次要问题（无需操作）
- **33 个死模型占 abilities 表** — 这些是 enabled=0 的记录，会在渠道恢复时自动复活，无需手动清理
- **anyrouter 0 models** — 设计如此，anyrouter 仅用于特定 Claude 路由

## 变更清单

### ~/.omp/agent/config.yml
```diff
modelRoles:
  slow: zg-newapi-anthropic/claude-opus-5:xhigh
- plan: zg-newapi/k3:max
+ plan: zg-newapi/glm-5.3:max
  commit: zg-newapi/u2-flash
  tiny: zg-newapi/agnes-2.5-flash
  vision: zg-newapi/dots-3-note-prev
  smol: zg-newapi/u2-flash
- designer: zg-newapi/k3:max
- task: zg-newapi/k3:max
+ designer: zg-newapi/glm-5.3:max
+ task: zg-newapi/glm-5.3:max
  advisor: zg-newapi/glm-5.3
  default: zg-newapi/qwen3.8-flash-next:high
```

### ~/.omp/agent/models.yml
- zg-newapi: 65 → 57 models（-8 dead entries）
- 移除：k3, glm-5.3-flash, gpt-6-sol, grok-4.6, qwen3.8-max, agnes-2.5-pro, gpt-6-luna, ling-3.3-flash-free

### 备份
- `~/.omp/agent/config.yml.bak-before-governance-20261009`
- `~/.omp/agent/models.yml.bak-before-governance-20261009`

## 验证结果

### Role Validation
```
✓ advisor: zg-newapi/glm-5.3 (5 live channels)
✓ commit: zg-newapi/u2-flash (1 live channels)
✓ default: zg-newapi/qwen3.8-flash-next:high (1 live channels)
✓ designer: zg-newapi/glm-5.3:max (5 live channels)
✓ plan: zg-newapi/glm-5.3:max (5 live channels)
✓ slow: zg-newapi-anthropic/claude-opus-5:xhigh (3 live channels)
✓ smol: zg-newapi/u2-flash (1 live channels)
✓ task: zg-newapi/glm-5.3:max (5 live channels)
✓ tiny: zg-newapi/agnes-2.5-flash (2 live channels)
✓ vision: zg-newapi/dots-3-note-prev (1 live channels)
```

### Smoke Test
```
summary: ALL OK
```

## 统计

| 指标 | 治理前 | 治理后 |
|------|--------|--------|
| OMP models (zg-newapi) | 65 | 57 |
| OMP 悬空条目 | 8 | 0 |
| 失效角色 | 3 (plan/designer/task) | 0 |
| NewAPI 死模型 | 33 | 33 (无变化，自动恢复机制) |
