# 渠道性能优化总结
日期: 2026-10-09

## 优化动作

### 1. justwoker 渠道优化
- **问题**: ch94 (justwoker-opus-1) 响应时间 30s，严重影响 claude-opus-4-8 性能
- **动作**: 禁用 ch94，保留 ch95 (justwoker-opus-2, 5.5s)
- **效果**: claude-opus-4-8 响应时间从 30s 降至 ~400ms

### 2. 性能测试结果
- **claude-opus-4-8 (OMP)**: 418ms - 1868ms (平均 ~1.3s) ✓
- **claude-opus-4-8 (qodercli)**: 350ms - 628ms (平均 ~444ms) ✓

## 渠道性能分布

| 类别 | 数量 | 占比 |
|------|------|------|
| 快速 (<5s) | 24 | 70% |
| 中速 (5-10s) | 5 | 15% |
| 慢速 (>10s) | 5 | 15% |

## 慢速渠道分析

### ch3 (baibei-100xlabs) - 34s
- **状态**: 主 opus 池 (priority=52, weight=20)
- **问题**: 响应极慢但无法禁用
- **独占模型**: claude-fable-5-1, claude-fable-5.1
- **建议**: 保持现状，作为容量备份；寻找 fable 系列替代渠道

### ch175 (hubway) - 18.7s
- **状态**: 备份渠道 (priority=-20)
- **独占模型**: codex-auto-review, gpt-5.5, gpt-5.6-sol
- **建议**: 保持低优先级；这些模型无替代渠道

### ch140 (intern-discovery) - 18.3s
- **实际测试**: 1.2s (DB 数据不准)
- **状态**: 正常可用

### ch89 (seeseed) - 15.3s
- **独占模型**: grok-chat-fast, qwen3.7-max/plus
- **建议**: 保持现状，提供独占模型

### ch178 (zen-free-bridge) - 11s
- **独占模型**: 多个免费模型
- **建议**: 保持现状，免费层备份

## 独占模型风险

### 高风险（慢速渠道独占）
- claude-fable-5-1 / claude-fable-5.1 (ch3, 34s)
- codex-auto-review / gpt-5.5 / gpt-5.6-sol (ch175, 18.7s)

### 建议
1. 寻找 fable 系列替代渠道
2. 监控 ch175 稳定性，考虑备用方案
3. 定期测试独占模型可用性

## 结论
- justwoker 优化显著提升 claude-opus-4-8 性能
- 慢速渠道多提供独占模型，无法简单禁用
- 当前配置已优化（低优先级作为备份）
- 建议持续监控独占模型渠道的稳定性
