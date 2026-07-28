# NewAPI VPS 极简状态（2026-07-28）

> 本次清理后，VPS 上只剩 NewAPI 容器 + joycode-proxy，所有优化层、路由脚本、TG 报警、guard 代理均已移除。

## 当前状态

| 组件 | 状态 | 说明 |
|------|------|------|
| NewAPI 容器 | 运行中 | `podman new-api`，监听 `0.0.0.0:3000` |
| nginx 反代 | 运行中 | 只保留 `newapi.aliyun.donglicao.com.conf` |
| joycode-proxy | 运行中 | `systemctl joycode-proxy.service`，监听 `127.0.0.1:34891` |
| kiro-guard | 已移除 | 10 个 systemd 服务已停止并移出 `/etc/systemd/system` |
| newapi-tg-bot | 已移除 | Telegram 报警服务已停止并移除 |
| 路由/优化脚本 | 已移除 | route_optimizer、unified_router、health_check、newapi_monitor、autoweight 等 |
| cron 任务 | 已清理 | 只保留 DB 备份、SSL 续期、lima-monitor 健康检查、网站同步 |

## DB 状态

```text
channels: 2
abilities: 5
```

## 当前渠道

| ID | 名称 | base_url | 模型 | 优先级 | 权重 |
|---|---|---|---|---|---|
| 1 | joycode-proxy-jd | `http://127.0.0.1:34891` | `JoyAI-Code` | 50 | 10 |
| 2 | ai.centos.hk-gpt | `https://ai.centos.hk` | `gpt-5.5`, `gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra` | 50 | 10 |

## 保留服务

### joycode-proxy（JD 模型）

- 监听：`127.0.0.1:34891`
- 路径：`/opt/joycode/JoyCode2Api`
- 连通性：已测试，POST `/v1/chat/completions` 可返回响应
- 在 NewAPI 添加渠道时：base_url 填 `http://127.0.0.1:34891`，按 JoyCode 支持的模型配置

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
