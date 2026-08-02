# Guardian 自愈系统 + centos 渠道运维记录 (2026-08-02)

**Status:** Active
**Scope:** NewAPI Guardian 自愈系统、centos 渠道管理、OMP 配置

## Guardian 当前状态

Guardian 核心闭环已实现；当前回归为 74/74 通过。现场已验证 Guardian/agentrouter 持续运行、Telegram `/help` 发送、渠道 45 权重恢复和本地代理路由。NewAPI 容器重启属于破坏性路径，本次未主动触发。
- 运行副本: `~/.omp/guardian/guardian.py`
- 仓库副本: `scripts/ops/guardian.py`

### 已实现功能

| 级别 | 功能 | 状态 |
|---|---|---|
| P0 | abilities 同步（PUT `/api/channel/` → `UpdateAbilities`） | 单测 + 源码路径确认 |
| P0 | OMP `modelRoles` 原子读写，并只恢复对应本地 provider 角色 | 单测通过 |
| P0 | 大池恢复限权：同模型健康同伴 ≥5 时，不超过同伴平均权重 | 单测通过 |
| P0 | 稳定性回滚：每 45s 检查，连续 2 次失败后禁用 `status=2` | 单测通过 |
| P0 | 权重历史还原、防抖动 3 次测 2 次通过、冷却/退避 | 单测通过 |
| P0 | 402/401/502 关键词硬错误扫描 | 代码 + 历史现场记录 |
| P1 | 慢结果按不同 `test_time` 去重，3 个独立慢结果才主动复测 | 单测 + 现场误降权复现后修复 |
| P1 | 全量扫描软错误连续 3 次才降权；成功清零 | 单测通过 |
| P1 | 性能窗口 20 个不同测试结果；降权后逐步恢复历史权重 | 单测通过 |
| P1 | Telegram 短轮询，HTML 占位符转义 | 单测 + 真实 `/help` 发送成功 |
| P2 | 余额趋势、日志轮转、metrics.json、定期 abilities 修复 | 单测/代码路径；未逐项做破坏性现场演练 |
| 运行 | agentrouter 使用 `100.83.32.95:8788`，watchdog 探测同一地址 | 真实 `/v1/models` 返回成功 |
| 运行 | Guardian 与 agentrouter watchdog 常驻，用户登录自动启动 | 当前进程 ready，Startup 入口已配置 |
| 运行 | NewAPI 容器重启需连续 3 次探测失败；成功后 30min 冷却，失败 60s 退避 | 单测通过；SSH 走 argv、无本地 podman fallback |
| 运行 | 本地代理自愈重启与告警冷却解耦（冷却只控通知） | 单测通过 |

### 已知限制

| # | 限制 | 优先级 |
|---|---|---|
| 1 | OMP 主动探测只报警，不自动切换角色，避免擅自改变用户路由 | 设计约束 |
| 2 | `_auto_adjust_weights` 需要 20 个不同 NewAPI `test_time` 样本 | P2 |
| 3 | 没有渠道组/角色池整体可用性判断 | P2 |
| 4 | 配置变更需要重启 Guardian | P2 |
| 5 | 现有计划任务设置受管理员权限保护；已增加用户 Startup 登录入口作为可控兜底 | 运维限制 |
| 6 | `_balance_pool_weights` 恢复时会额外调用一次 `get_channels()` | P2 |
| 7 | NewAPI 容器 SSH/podman 重启路径本次未做破坏性现场测试 | 风险说明 |

### 双主仲裁规则（NewAPI 自动启用 vs Guardian 恢复）

`AutomaticEnableChannelEnabled=true`（NewAPI 可自动启用）+ Guardian 恢复循环构成双主。
仲裁规则：**NewAPI 自动启用只负责"快速拉起"，Guardian 拥有"是否稳定加入"的最终决定权**。
Guardian 对 NewAPI 自动启用的渠道仍执行 3 次 `test_channel` 稳定验证，不通过即再次禁用
（`test_rechecks_and_re_disables_newapi_auto_enabled_channel`）。本规则属设计约束，不关闭
NewAPI 自动启用，避免丢失快速恢复路径。

## NewAPI 侧配置变更


| 设置 | 旧值 | 新值 | 原因 |
|---|---|---|---|
| `AutomaticDisableStatusCodes` | 历史值 | `401,402,403,502` | 当前 API 实读值 |
| `AutomaticDisableKeywords` | 7 条英文关键词 | 增加余额不足、INSUFFICIENT_BALANCE、credit balance | 覆盖中英文余额错误 |
| `AutomaticRetryStatusCodes` | 曾包含 402 | 当前明确排除 402 | 余额不足不做池内重试 |
| `AutomaticEnableChannelEnabled` | 历史值 | `true` | Guardian 对自动启用渠道仍做 3 次稳定验证 |
| `ChannelDisableThreshold` | 默认 | `3` | 连续失败阈值 |
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

### 凭据处理

历史文档中的明文 key 已移除。凭据只保存在本机 `~/.omp/guardian/secrets.json`、NewAPI 渠道配置或环境变量中；已经进入版本历史的旧 key 应视为已暴露并轮换。

## 错误渠道扫描实测

```
ch37 tokenrhythm-glm-1: 402 余额不足 → status 1→2 自动禁用 ✓
ch17 openoneapi-grok:   401 无效的令牌 → status 1→2 自动禁用 ✓
```

## 相关文档

- Guardian README: `scripts/ops/README.md`（运行时镜像 `~/.omp/guardian/README.md`）
- Guardian 代码: `scripts/ops/guardian.py`
- Ops 约束: `docs/ops/do-not-modify-cc-switch.md`