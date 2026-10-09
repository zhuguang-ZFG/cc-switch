# OMP 嵌入模型配置文档
日期: 2026-10-09

## 概述
OMP 语义搜索功能已启用，使用 muyuan-gongyi (ch173) 提供的嵌入模型。

## 配置详情

### OMP 配置 (config.yml)
```yaml
memory:
  backend: mnemopi
mnemopi:
  noEmbeddings: false  # 已启用
  polyphonicRecall: true
  proactiveLinking: true
  enhancedRecall: true
  embeddingModel: zg-newapi/mistral-embed  # 默认嵌入模型
```

### 可用嵌入模型

| 模型 | 维度 | 渠道 | 状态 |
|------|------|------|------|
| mistral-embed | 1024 | ch173 (muyuan-gongyi) | ✓ 默认 |
| codestral-embed | 1536 | ch173 (muyuan-gongyi) | ✓ 可用 |
| mistral-embed-2312 | 1024 | ch173 (muyuan-gongyi) | ✓ 可用 |
| codestral-embed-2505 | 1536 | ch173 (muyuan-gongyi) | ✓ 可用 |

### NewAPI 渠道配置
- **渠道**: ch173 (muyuan-gongyi)
- **优先级**: 20
- **权重**: 1
- **端点**: `/v1/embeddings`
- **状态**: 启用

## 性能基线

### 延迟测试 (2026-10-09)
| 模型 | 延迟 | 维度 |
|------|------|------|
| mistral-embed | 974ms | 1024 |
| codestral-embed | 1069ms | 1536 |
| mistral-embed-2312 | 795ms | 1024 |
| codestral-embed-2505 | 901ms | 1536 |

**平均延迟**: 934ms  
**状态**: ALL STABLE

## 监控

### 稳定性检查脚本
```bash
python3 scripts/ops/monitor-embedding-stability.py
```

### 监控指标
- 成功率：目标 100%
- 延迟：目标 < 2000ms
- 维度一致性：1024d 或 1536d

## 备用方案

当前嵌入模型仅由 ch173 (muyuan-gongyi) 提供，无备用渠道。

**风险**: 如果 ch173 不可用，语义搜索功能将失效。

**建议**:
1. 定期运行稳定性监控
2. 寻找其他提供嵌入模型的渠道作为备份
3. 考虑缓存常用嵌入结果以减少调用

## 使用场景

OMP 语义搜索用于：
- 记忆检索（mnemopi backend）
- 代码片段语义匹配
- 文档相似度计算
- 上下文增强召回

## 故障排除

### 嵌入调用失败
1. 检查 ch173 渠道状态：`python3 -c "import sqlite3; ..."`
2. 运行稳定性检查脚本
3. 检查 NewAPI 日志
4. 验证 API key 有效性

### 延迟过高
1. 检查网络连接
2. 验证 muyuan-gongyi 上游状态
3. 考虑切换到更快的嵌入模型（如 mistral-embed-2312）

## 更新历史

- 2026-10-09: 初始配置，启用语义搜索，配置 4 个嵌入模型
