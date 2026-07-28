# NewAPI VPS 极简状态（2026-07-28）

> 本次清理后，VPS 上只剩 NewAPI 容器 + joycode-proxy，所有优化层、路由脚本、TG 报警、guard 代理均已移除。

## 当前状态

| 组件 | 状态 | 说明 |
|------|------|------|
| NewAPI 容器 | 运行中 | `podman new-api`，监听 `0.0.0.0:3000` |
| nginx 反代 | 运行中 | 只保留 `newapi.aliyun.donglicao.com.conf` |
| joycode-proxy | 已放弃 | `systemctl joycode-proxy.service` 仍在跑，但 NewAPI 渠道已被用户删除（JD 账号掉登录，无法认证） |
| kiro-guard | 已移除 | 10 个 systemd 服务已停止并移出 `/etc/systemd/system` |
| newapi-tg-bot | 已移除 | Telegram 报警服务已停止并移除 |
| 路由/优化脚本 | 已移除 | route_optimizer、unified_router、health_check、newapi_monitor、autoweight 等 |
| cron 任务 | 已清理 | 只保留 DB 备份、SSL 续期、lima-monitor 健康检查、网站同步 |

## DB 状态

```text
channels: 5
abilities: 13
```

## 当前渠道

| ID | 名称 | base_url | 模型 | 状态 | 优先级 | 权重 |
|---|---|---|---|---|---|---|
| 2 | ai.centos.hk-gpt | `https://ai.centos.hk` | `gpt-5.5`, `gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra` | 启用 | 50 | 10 |
| 3 | baibei-100xlabs | `https://sub.100xlabs.space` | `claude-opus-4-7`, `claude-opus-4-8`, `claude-opus-5` | 启用（单渠道 6 key 轮询） | 50 | 20 |
| 9 | linxi-k40 | `https://k40.shengqainbang.cn` | `claude-opus-4-7`, `claude-opus-4-8`, `claude-opus-5` | 启用（单渠道 3 key 轮询） | 50 | 10 |
| 12 | vyceai | `https://vyceai.com` | `claude-haiku-4-5`, `claude-sonnet-4-6`, `claude-sonnet-5`, `claude-fable-5`, `glm-5.2`, `gpt-5.6-sol`, `deepseek-v4-flash`, `gemini-3.1-flash-lite`, `gemini-3.6-flash`, `mimo-v2.5-pro`, `minimax-m3`, `nemotron-ultra-550b`, `nemotron-vision`, `auto` | 启用 | 50 | 10 |
| 13 | ai.168661-grok | `https://ai.168661.xyz` | `grok-4.5` | 启用 | 50 | 10 |

- 百倍 6 个 key、林夕 3 个 key 已改用 NewAPI **单渠道多 key 模式**（key 字段换行分隔，内部自动轮询），渠道数从 12 收敛到 5
- 权重调优（2026-07-28）：opus 池按 key 容量配权——百倍 w20（6 key）：林夕 w10（3 key）≈ 2:1 流量比；vyceai 从 0/0 修回 50/10（0 权重会导致其独占模型 haiku/sonnet 等完全调度不到）
- vyceai 实际挂了 14 个模型（用户在 UI 扩充），其中 `glm-5.2`、`gpt-5.6-sol` 与其他渠道重名，NewAPI 按权重在多渠道间随机调度
- joycode-proxy-jd 渠道已由用户从 NewAPI 删除（JD 账号掉登录 + 风控无法恢复，已放弃）
- ai.168661-grok 直连实测：`/v1/models` 返回 grok-4.3/4.5/chat-fast/imagine-image 四款，按需只挂了 `grok-4.5`；`chat/completions` 实测 HTTP 200（首响约 10s）

## 保留服务

### joycode-proxy（JD 模型，已放弃）

- 监听：`127.0.0.1:34891`
- 路径：`/opt/joycode/JoyCode2Api`
- 当前状态：代理服务仍在运行，但 NewAPI 渠道已被用户删除
- 原因：JD 账号凭证过期，扫码/OAuth 均触发风控（riskCode=1100），无法完成认证
- 恢复方式：需本机 JoyCode IDE `state.vscdb` 或有效的 ptKey

### 基础运维 cron

```text
10 8 * * *   donglicao 网站学习流同步 + pm2 重启
*/5 * * * *  /opt/lima-monitor/health_check.sh
@reboot      /usr/local/bin/fix-tailscale-firewall.sh
0 3 * * *    /opt/lima-monitor/scripts/renew_ssl.sh
17 3 * * *   /opt/new-api/backup_db.sh
```

## 已移除文件存放位置

如需恢复，可在 VPS 上查看：

- `/opt/new-api/removed-systemd-20260728-161456/` — systemd 服务文件
- `/opt/new-api/removed-scripts-20260728-161638/` — Python 脚本、env、日志、metrics
- `/opt/new-api/removed-nginx-20260728-161657/` — zhipu-coding-shim.conf

## 备份

- DB 备份：`/opt/new-api/data/backups/one-api.before-remove-optimizations-20260728-160859.db`
- 渠道配置备份：`/opt/new-api/data/backups/channels-before-clear-20260728-161300.json`
- 旧 crontab 备份：`/opt/new-api/data/backups/crontab-before-clear-20260728-161530.txt`

## 验证

```text
curl -s -m 10 https://aliyun.donglicao.com/api/status
# HTTP 200
```

## 历史文档

此前的路由快照、优化方案、补丁记录见 `docs/ops/zg-claude-routing.md`（已标注为历史）。
