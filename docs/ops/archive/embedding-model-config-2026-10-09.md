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

## 备用渠道调查结果 (2026-10-09)

### 调查范围
- 测试了 35 个启用渠道
- 测试了 8 个常见嵌入模型名称（OpenAI、BGE、E5、Nomic 等）
- 检查了 Mistral、OpenAI、Azure 等提供商渠道

### 结果
**唯一可用渠道**: ch173 (muyuan-gongyi)
- 提供 4 个嵌入模型
- 平均延迟: 1037ms
- 状态: ALL STABLE

**无备用渠道**: 当前 NewAPI 配置中无其他渠道提供嵌入模型。

### 故障转移方案

由于 OMP 仅支持单一 `embeddingModel` 配置，无法自动故障转移。

**手动故障转移步骤**:
1. 如果 mistral-embed 不可用，编辑 `~/.omp/agent/config.yml`
2. 将 `embeddingModel` 改为其他可用模型：
   ```yaml
   embeddingModel: zg-newapi/codestral-embed  # 1536d
   # 或
   embeddingModel: zg-newapi/mistral-embed-2312  # 1024d, 最快
   # 或
   embeddingModel: zg-newapi/codestral-embed-2505  # 1536d
   ```
3. 重启 OMP 或重新加载配置

**推荐优先级**（基于延迟测试）:
1. mistral-embed-2312 (795ms) - 最快
2. codestral-embed-2505 (901ms)
3. mistral-embed (974ms) - 当前默认
4. codestral-embed (1069ms)

### 长期建议

1. **寻找备用渠道**: 联系 NewAPI 管理员添加其他嵌入模型渠道
2. **缓存策略**: 实现嵌入结果缓存，减少对单一渠道的依赖
3. **监控告警**: 将 `monitor-embedding-stability.py` 集成到 Guardian 监控
4. **OMP 功能请求**: 向 OMP 开发团队请求支持嵌入模型故障转移列表

### 当前风险评估

- **风险等级**: 中
- **影响**: 如果 ch173 不可用，语义搜索功能完全失效
- **恢复时间**: 手动修改配置 ~2 分钟
- **缓解措施**: 定期监控，快速手动切换
