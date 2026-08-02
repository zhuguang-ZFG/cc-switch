# NewAPI Guardian — 自愈监控系统

完整的健康检查、自动修复、Telegram 报警、每日报告、权重闭环自愈系统。

## 功能

- **健康检查**: 15 秒间隔监控 NewAPI、渠道、本地代理
- **错误渠道扫描**: 每 5 分钟分批主动探测 402/401/502 等瞬间返回的错误（慢渠道检测无法覆盖）
- **自动修复**: 慢渠道降权→禁用、错误渠道直接禁用、崩溃代理自动重启、NewAPI 容器自动重启
- **权重闭环自愈**: 渠道恢复 → 自动启用 → 加入聚合池 → 权重调整 → 性能监控 → 自动降权/禁用
- **OMP 角色联动**: 渠道恢复时更新 `modelRoles`；每 20 分钟主动探测本地/Tailscale 角色端点并报警
- **Telegram 报警**: 实时报警、每日健康报告、余额趋势预警
- **指标导出**: JSON metrics 文件，可与其他监控系统集成

## 监控指标

| 指标 | 阈值 | 动作 |
|---|---|---|
| NewAPI 健康 | `/api/status` 连续 3 次非 200 | 重启容器（成功后 30min 冷却，失败 60s 退避；重启后验证最多阻塞 30s） |
| 渠道响应时间 | 3 个不同测试结果均 > 60 秒，且主动复测失败 | 降权 → 禁用 |
| 渠道错误码 | 402/401/502 + 关键词匹配 | 直接禁用 |
| 本地代理健康 | 无响应 | 自动重启 |
| 错误率 | > 10% | 报警 |
| 余额 | < 100 万 quota | 报警 |
| 余额趋势 | 预计 < 24h 耗尽 | 预警 |

## 权重闭环自愈流程

```
渠道故障
  ├─ 慢渠道: 3 个不同 test_time 均 > 60s，主动复测失败后降权
  ├─ 全量扫描软错误: 连续 3 次失败后降权
  ├─ 硬错误: 402/401/502 + 关键词匹配后直接禁用
  └─ 禁用后
       ├─ 至少等待 5 分钟冷却，并按失败次数指数退避
       ├─ 恢复验证: 3 次 test_channel，至少 2 次通过
       ├─ 自动启用 + 加入聚合池
       │    ├─ 从 weight_history 恢复权重/优先级
       │    ├─ PUT /api/channel/ 同步 abilities
       │    ├─ 同模型健康渠道 ≥5 时，恢复权重不超过同伴平均值
       │    └─ 仅为对应本地 provider 恢复 OMP 角色
       └─ 加入后 10 分钟稳定性监控
            ├─ 每 45 秒 test_channel
            ├─ 连续 2 次失败 → 禁用 status=2，并重新进入恢复队列
            └─ 稳定 → 清理监控记录
```

## 自动权重调整

| 条件 | 动作 |
|---|---|
| 20 个不同测试结果的成功率 < 80% | 降权 (weight × 0.5) |
| 20 个不同测试结果的平均响应 > 45s | 降权 (weight × 0.5) |
| 已降权渠道成功率 ≥ 95% 且响应 < 10s | 每个完整窗口加权 1，最多恢复到历史权重 |
| 权重已降至 1 仍不健康 | 禁用 |

## 错误渠道扫描

`check_channel` 只检查 `response_time`，402 余额不足瞬间返回（rt < 1s）不会触发慢渠道检测。
`scan_error_channels` 每 5 分钟分批用 `test_channel` 主动探测，匹配以下关键词后自动禁用：

- 余额不足 / INSUFFICIENT_BALANCE / credit balance / quota
- 402 / 401 / invalid

## NewAPI 侧配置

Guardian 依赖以下 NewAPI 设置（已通过 API 配置）：

| 设置 | 值 | 说明 |
|---|---|---|
| `AutomaticDisableStatusCodes` | `401,402,403,502` | 自动禁用的 HTTP 状态码 |
| `AutomaticDisableKeywords` | 余额不足, INSUFFICIENT_BALANCE, credit balance, ... | 自动禁用的错误关键词 |
| `AutomaticEnableChannelEnabled` | `true` | NewAPI 可自动启用；Guardian 会重新验证并在不稳定时禁用 |
| `AutomaticRetryStatusCodes` | `100-199,300-399,409-499,500-504,505-599` | 明确排除无意义的 402 池内重试 |
| `ChannelDisableThreshold` | `3` | NewAPI 连续失败阈值 |
| `RetryTimes` | `2` | 请求重试次数 |

## 安装

### 1. 配置

将密钥写入 `~/.omp/guardian/secrets.json`，或使用同名大写环境变量覆盖：

```json
{
  "newapi_base": "https://your-newapi.example",
  "newapi_token": "...",
  "newapi_user": "1",
  "telegram_token": "...",
  "telegram_allowed_users": "5345665818",
  "codebuddy_api_key": "..."
}
```

`telegram_allowed_users` 为逗号分隔的 Telegram 用户 ID 白名单；未配置时仅接受私聊 owner
（`from.id == chat.id`），群组成员命令一律拒绝。


### 2. 启动

```bash
python guardian.py
```

Windows 当前使用两个用户登录入口：现有 `NewAPI Guardian` 计划任务，以及 Startup 目录中的 `cline-glm-proxy.bat`。`start.bat` 自带退出重启循环，Guardian 进程互斥锁会拒绝重复实例。

## Telegram 命令

- `/status` — 查看当前系统状态（含余额趋势）
- `/channels` — 列出所有渠道状态（含权重）
- `/report` — 立即生成健康报告
- `/restart <proxy>` — 手动重启本地代理
- `/enable <channel_id>` — 手动启用渠道
- `/disable <channel_id>` — 手动禁用渠道
- `/help` — 显示帮助

## 文件

| 文件 | 说明 |
|---|---|
| `~/.omp/guardian/guardian.py` | 主程序 |
| `~/.omp/guardian/secrets.json` | 本机密钥配置，不应提交版本库 |
| `~/.omp/guardian/guardian.log` | 日志（RotatingFileHandler, 5MB × 5） |
| `~/.omp/guardian/state.json` | 状态（禁用/降权/加入/权重历史） |
| `~/.omp/guardian/metrics.json` | 指标导出 |
| Startup `cline-glm-proxy.bat` | 登录时启动代理 watchdog 与 Guardian |

## 安全

- Telegram 命令鉴权：chat_id + 发送者白名单（`telegram_allowed_users`）双重校验，群组成员默认拒绝
- NewAPI 容器重启需连续 3 次探测失败才触发，避免瞬态抖动误重启；SSH 走 argv 调用，无本地 podman fallback
- 所有自愈动作都发送 Telegram 通知，可随时人工干预
- 自动禁用/启用渠道需要 NewAPI 管理权限

## 参考

- NewAPI 源码: https://github.com/QuantumNous/new-api
- NewAPI Channel.Update() → UpdateAbilities(nil): abilities 表自动同步证据
- OpenClaw WatchDog: https://clawhub.ai/abdullah4ai/openclaw-watchdog