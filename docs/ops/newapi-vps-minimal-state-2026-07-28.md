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

## 容器环境变量（2026-07-28 重建容器时加入）

| 变量 | 值 | 理由 |
|---|---|---|
| `GLOBAL_API_RATE_LIMIT` | `600` | 默认 180 次/3 分钟/单 IP，Claude Code 突发（主对话+分类器+子代理）易触发 429 |
| `ERROR_LOG_ENABLED` | `true` | 前端日志显示上游错误细节，排查"停止/报错"不用猜 |
| `CHANNEL_TEST_FREQUENCY` | `30` | 每 30 分钟自动探测渠道，坏 key 提前踢出轮询（配合已开启的自动禁用/恢复） |
| `MEMORY_CACHE_ENABLED` | `true` | SQLite 单实例减读压 |
| `BATCH_UPDATE_ENABLED` | `true` | quota/日志聚合写盘，减 SQLite 写压 |
| `TZ` | `Asia/Shanghai` | 日志时间戳可读 |

明确不动的：`RELAY_TIMEOUT=0`（官方警告设短会导致计费不同步）、`STREAMING_TIMEOUT` 默认 300s、`RetryTimes=4`。

重建命令（数据在 `/opt/new-api/data` 挂载卷，重建不丢数据）：

```bash
podman run -d --name new-api --restart always -p 3000:3000 -v /opt/new-api/data:/data \
  -e TZ=Asia/Shanghai -e GLOBAL_API_RATE_LIMIT=600 -e ERROR_LOG_ENABLED=true \
  -e CHANNEL_TEST_FREQUENCY=30 -e MEMORY_CACHE_ENABLED=true -e BATCH_UPDATE_ENABLED=true \
  docker.m.daocloud.io/calciumion/new-api:latest
```

## DB 状态

```text
channels: 5
abilities: 13
```

## 当前渠道

| ID | 名称 | base_url | 模型 | 状态 | 优先级 | 权重 |
|---|---|---|---|---|---|---|
| 2 | ai.centos.hk-gpt | `https://ai.centos.hk` | `gpt-5.5`, `gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra` | 启用 | 50 | 10 |
| 3 | baibei-100xlabs | `https://sub.100xlabs.space` | `claude-opus-4-7`, `claude-opus-4-8`, `claude-opus-5` | 启用（单渠道 6 key 轮询） | 50 | 10 |
| 9 | linxi-k40 | `https://k40.shengqainbang.cn` | `claude-opus-4-7`, `claude-opus-4-8`, `claude-opus-5` | 启用（单渠道 3 key 轮询） | 50 | 20 |
| 12 | vyceai | `https://vyceai.com` | `claude-haiku-4-5`, `claude-sonnet-4-6`, `claude-sonnet-5`, `claude-fable-5`, `glm-5.2`, `gpt-5.6-sol`, `deepseek-v4-flash`, `gemini-3.1-flash-lite`, `gemini-3.6-flash`, `mimo-v2.5-pro`, `minimax-m3`, `nemotron-ultra-550b`, `nemotron-vision`, `auto` | 启用 | 50 | 10 |
| 13 | ai.168661-grok | `https://ai.168661.xyz` | `grok-4.5` | 启用 | 50 | 10 |

- 百倍 6 个 key、林夕 3 个 key 已改用 NewAPI **单渠道多 key 模式**（key 字段换行分隔，内部轮询），渠道数从 12 收敛到 5
- **多 key 渠道的坑（2026-07-28 踩过）**：只在 `key` 字段换行不够，还必须把 `channel_info` 列写成 **BLOB** JSON 并带 `"is_multi_key": true` + `multi_key_size` + `multi_key_mode`，否则 Anthropic 渠道把整段多行 key 塞进 `X-Api-Key` 头，报 `invalid header field value` 秒 500。且必须用 `sqlite3.Binary` 写 BLOB——写 TEXT 时 Go 端 `value.([]byte)` 断言失败，scan 报 `unexpected end of JSON input`，`is_multi_key` 静默为 false。单 key 渠道也要写 `{"is_multi_key":false,...}` BLOB，NULL 同样触发 scan 错误
- 权重调优（2026-07-28，按 VPS 实测速度配权，谁快谁多拿流量）：
  - opus 池：林夕 w20（3 次采样 2.7s/2.9s/4.7s，稳定快）：百倍 w10（12.7s/12.7s/2.8s，波动大）≈ 2:1，林夕主力、百倍兜底+扩容量
  - `gpt-5.6-sol` 双渠道重名：centos ability w10（实测 5.0s）主力，vyceai ability w2（实测 14.8s）仅冗余
  - vyceai 渠道级从 0/0 修回 50/10（0 权重会导致其独占模型 haiku/sonnet 等完全调度不到）
  - 单源渠道（centos 其余 gpt、grok、vyceai 独占模型）权重不影响调度，保持 10
  - 基准参考：centos gpt-5.5 5.0s / grok-4.5 2.3s（波动大，另一次 10.4s）/ vyceai sonnet-4-6 14.8s
- vyceai 实际挂了 14 个模型（用户在 UI 扩充），其中 `glm-5.2`、`gpt-5.6-sol` 与其他渠道重名，NewAPI 按权重在多渠道间随机调度
- vyceai 稳定性提醒（2026-07-28 晚实测）：`glm-5.2`/`mimo-v2.5-pro` 60s 超时、`claude-haiku-4-5` 45.9s——公益站上游波动大，`CHANNEL_TEST_FREQUENCY=30` 会自动摘除持续失败的渠道，无需手动干预
- Kimi Code CLI 已直连本 NewAPI（`http://47.112.162.80:3000/v1`，公网 40ms；弃用 Tailscale 路径 3.2s），客户端模型清单已与实有模型同步
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
