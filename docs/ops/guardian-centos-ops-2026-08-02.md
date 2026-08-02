# Guardian 自愈系统 + centos 渠道运维记录 (2026-08-02)

**Status:** Active
**Scope:** NewAPI Guardian 自愈系统、centos 渠道管理、OMP 配置

## Guardian 当前状态

Guardian 已完成 P0/P1/P2 全部修复（16/16 验证通过），代码位于：
- 运行副本: `~/.omp/guardian/guardian.py`
- 仓库副本: `scripts/ops/guardian.py`

### 已实现功能

| 级别 | 功能 | 状态 |
|---|---|---|
| P0 | abilities 表自动同步（PUT /api/channel/ → UpdateAbilities） | ✅ |
| P0 | OMP config.yml 真正读写（re.sub 替换 + 插入新角色） | ✅ |
| P0 | 负载均衡（>5 渠道时按比例 scale=0.9） | ✅ |
| P0 | 回滚机制（每 45s 检查，2 次失败 → weight=0） | ✅ |
| P0 | 权重历史还原（恢复时从 weight_history 还原） | ✅ |
| P0 | 防抖动（3x test_channel, ≥2 通过, 5min 冷却） | ✅ |
| P0 | 错误渠道扫描（402/401/502 瞬间返回错误检测） | ✅ |
| P1 | 渠道性能监控（deque maxlen=20） | ✅ |
| P1 | 自动降权（weight×0.5, 最小 1, 仍不健康则禁用） | ✅ |
| P1 | 权重自动调整（成功率/响应时间驱动） | ✅ |
| P1 | Telegram 非阻塞（getUpdates timeout=1） | ✅ |
| P2 | 余额趋势分析（消耗速度 + 预计耗尽时间） | ✅ |
| P2 | 日志轮转（RotatingFileHandler 5MB×5） | ✅ |
| P2 | 指标导出（metrics.json） | ✅ |
| P2 | NewAPI status API（POST /api/channel/{id}/status） | ✅ |
| P2 | fix abilities API（POST /api/channel/fix） | ✅ |

### 已知限制

| # | 限制 | 优先级 |
|---|---|---|
| 1 | `_update_omp_roles` 只在渠道恢复时触发，不主动检测 OMP 角色是否指向死渠道 | P2 |
| 2 | `_auto_adjust_weights` 需要 3 个样本（~45s）才开始工作 | P2 |
| 3 | 没有渠道分组/角色感知（某角色所有渠道故障时无法感知） | P2 |
| 4 | 没有配置热加载（配置变更需重启） | P2 |
| 5 | Guardian 进程未被配置为 Windows 服务/任务计划 | P1 |
| 6 | `_balance_pool_weights` 每次调用都 get_channels()（重复 API 请求） | P2 |

## NewAPI 侧配置变更

| 设置 | 旧值 | 新值 | 原因 |
|---|---|---|---|
| `AutomaticDisableStatusCodes` | `401` | `401,402,403` | 402 余额不足不触发自动禁用 |
| `AutomaticDisableKeywords` | 7 条英文关键词 | += 余额不足, INSUFFICIENT_BALANCE, credit balance | 中文错误消息不匹配 |
| `ChannelDisableThreshold` | 默认 | `3` | 个人使用，较少渠道，更快故障检测 |
| `RetryTimes` | 默认 | `2` | 更快故障转移 |

## centos 渠道变更 (2026-08-02)

### 连接测试

| 线路 | URL | 状态 | 延迟 |
|---|---|---|---|
| 主线路 | `https://ai.centos.hk` | ✗ 403 → 超时 | 不可用 |
| EO加速 | `https://api.centos.hk` | ✓ | ~900ms / ~6700ms |
| 三网优化 | `https://frapi.centos.hk` | ✓ | ~1100ms / ~7500ms |

### 渠道操作

| 操作 | 渠道 | 说明 |
|---|---|---|
| 新增 | ch62 `centos-eo-gpt` | EO加速, gpt-5.5/gpt-5.6-sol, weight=10, status=1 |
| 新增 | ch63 `centos-fr-gpt` | 三网优化, gpt-5.5/gpt-5.6-sol, weight=10, status=1 |
| 禁用 | ch2 `ai.centos.hk-gpt` | 主线路 403→超时，已更新 key 但不可用 |
| 删除 | ch16 `centos-api-backup-gpt` | 与 ch62 同 URL 冗余 |
| 删除 | ch25 `centos-api-newkey-gpt` | 与 ch62 同 URL 冗余 |

### 新 key

```
sk-B3ha7RRJ9LdG0NGQylkvJalYDgF7eYN3MvqX9bcz1wFpcIMv
```

## 错误渠道扫描实测

```
ch37 tokenrhythm-glm-1: 402 余额不足 → status 1→2 自动禁用 ✓
ch17 openoneapi-grok:   401 无效的令牌 → status 1→2 自动禁用 ✓
```

## 相关文档

- Guardian README: `~/.omp/guardian/README.md`
- Guardian 代码: `scripts/ops/guardian.py`
- Ops 约束: `docs/ops/do-not-modify-cc-switch.md`