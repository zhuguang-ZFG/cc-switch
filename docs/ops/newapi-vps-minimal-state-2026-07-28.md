# NewAPI VPS 极简状态（2026-07-28）

> 本次清理后，VPS 上只剩 NewAPI 容器 + joycode-proxy，所有优化层、路由脚本、TG 报警、guard 代理均已移除。晚间的 OpenOneAPI、Kimi MCP 和 Claude Agent 修复见 [newapi-kimi-mcp-claude-current-state-2026-07-28.md](./newapi-kimi-mcp-claude-current-state-2026-07-28.md)。
>
> **2026-07-29 更新**：渠道表与 Grok 现状以 [newapi-audit-2026-07-29.md](./newapi-audit-2026-07-29.md) 为准。本文下文把 channel 15 写作 Grok 相关源已过时。Channel 15 现仅挂 `deepseek-v4-flash` / `sensenova-6.7-flash-lite`，其 `glm-5.2` 因长上下文持续触发 500 万 TPM 限流已摘除。Channel 20 `fengwind-grok` 虽保留 priority 70，但现已手动停用（`grok-4.5` 连续测试 502）；当前成功的 Grok 4.5 流量走 channel 17。

## 当前状态

| 组件 | 状态 | 说明 |
|------|------|------|
| NewAPI 容器 | 运行中 | `podman new-api`，监听 `0.0.0.0:3000` |
| nginx 反代 | 运行中 | 实际服务域名 `aliyun.donglicao.com`（apex，证书有效）；`newapi.aliyun.donglicao.com` 子域名无证书无 server 块已废弃，NewAPI 的 ServerAddress/api_info 均指向 apex |
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

## 渠道亲和（2026-07-28 开启）

`channel_affinity_setting.enabled = true`。规则库此前已配好三条（claude/gpt/glm trace），本次仅开开关。

- 效果：同一会话（按 `metadata.user_id`/`prompt_cache_key`/UA 提取指纹）在 TTL 600s 内固定到同一上游渠道；渠道级粘性 + 渠道内 key 仍轮询
- 收益：claude 前缀缓存命中率提升（不再在林夕/百倍间随机跳）、长对话中途不换上游账户减少流中断
- 已验证：同一 `metadata.user_id` 两连发均落 channel #9（affinity key_fp 一致，rule `claude cli trace` 命中，第二次显式记录 `"channel_id":9`）
- 失败保护：`skip_retry_on_failure=false`，渠道故障仍走正常故障转移
- 附带：claude 规则的 `pass_headers` 模板会把客户端原始头（User-Agent、Anthropic-Beta、X-Stainless-*）透传上游

## 遗留惰性配置（不影响运行，勿清）

- `global.chat_completions_to_responses_policy`：channel_ids=[142] 指向已删除的旧渠道，当前惰性。这是「chat/completions → responses 协议转换」策略，日后若再遇 Codex 锁客户端的渠道（如 zzzcoding），把新渠道 id 填进去即可让 NewAPI 自动转协议——**这是解协议级锁的正解**，留着当模板
- `ModelRatio`/`CompletionRatio` 中过时模型条目：仅影响计费显示，无限额度自用无实际影响

重建命令（数据在 `/opt/new-api/data` 挂载卷，重建不丢数据）：

```bash
podman run -d --name new-api --restart always -p 3000:3000 -v /opt/new-api/data:/data \
  -e TZ=Asia/Shanghai -e GLOBAL_API_RATE_LIMIT=600 -e ERROR_LOG_ENABLED=true \
  -e CHANNEL_TEST_FREQUENCY=30 -e MEMORY_CACHE_ENABLED=true -e BATCH_UPDATE_ENABLED=true \
  docker.m.daocloud.io/calciumion/new-api:latest
```

## 晚间最终路由增量（覆盖下方早间快照）

- 新增 channel 17 `openoneapi-grok`：`grok-4.5` 主渠道，priority 60 / weight 30。
- channel 13 保留为 `grok-4.5` 备份，priority 50 / weight 10。
- channel 9 `linxi-k40` 改为 Claude Opus 5 单 Key 主渠道，priority 60 / weight 20。
- 新增 channel 18 `linxi-k40-opus5-backup`：Claude Opus 5 单 Key备份，priority 55 / weight 10。
- channel 3 只禁用 `claude-opus-5` ability，规避完整 Agent 请求触发的上游 WAF；其他模型不变。

准确配置、根因和回归结果见 [newapi-kimi-mcp-claude-current-state-2026-07-28.md](./newapi-kimi-mcp-claude-current-state-2026-07-28.md)。

## 早间 DB 快照（历史）

> 下表记录晚间 OpenOneAPI 接入和 Claude 单 Key 拆分前的状态，不能作为当前渠道清单执行。

```text
channels: 8
abilities: 32
```

## 早间渠道快照（历史）

| ID | 名称 | base_url | 模型 | 状态 | 优先级 | 权重 |
|---|---|---|---|---|---|---|
| 2 | ai.centos.hk-gpt | `https://ai.centos.hk` | `gpt-5.5`, `gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra` | 启用 | 50 | 10 |
| 3 | baibei-100xlabs | `https://sub.100xlabs.space` | `claude-opus-4-7`, `claude-opus-4-8`, `claude-opus-5` | 启用（单渠道 6 key 轮询） | 50 | 10 |
| 9 | linxi-k40 | `https://k40.shengqainbang.cn` | `claude-opus-4-7`, `claude-opus-4-8`, `claude-opus-5` | 启用（单渠道 3 key 轮询） | 50 | 20 |
| 12 | vyceai | `https://vyceai.com` | `claude-haiku-4-5`, `claude-sonnet-4-6`, `claude-sonnet-5`, `claude-fable-5`, `glm-5.2`, `deepseek-v4-flash`, `gemini-3.1-flash-lite`, `gemini-3.6-flash`, `mimo-v2.5-pro`, `minimax-m3`, `nemotron-ultra-550b`, `nemotron-vision`, `auto` | 启用 | 50 | 10 |
| 13 | ai.168661-grok | `https://ai.168661.xyz` | `grok-4.5` | 启用 | 50 | 10 |
| 14 | wintoken-glm | `https://www.wintoken.dev` | `glm-5.2` | 启用（单渠道 2 key 轮询） | 50 | 10 |
| 15 | sensenova-token | `https://token.sensenova.cn` | `sensenova-6.7-flash-lite`, `deepseek-v4-flash`, `glm-5.2` | 启用（单渠道 2 key 轮询） | 50 | 10 |
| 16 | centos-api-backup-gpt | `https://api.centos.hk` | `gpt-5.5`, `gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra` | 启用（centos 备用线路入口，同 key） | 50 | 5 |

- 百倍 6 个 key、林夕 3 个 key 已改用 NewAPI **单渠道多 key 模式**（key 字段换行分隔，内部轮询），渠道数从 12 收敛到 5（后新增 grok、wintoken 共 7 条）
- **多 key 渠道的坑（2026-07-28 踩过）**：只在 `key` 字段换行不够，还必须把 `channel_info` 列写成 **BLOB** JSON 并带 `"is_multi_key": true` + `multi_key_size` + `multi_key_mode`，否则 Anthropic 渠道把整段多行 key 塞进 `X-Api-Key` 头，报 `invalid header field value` 秒 500。且必须用 `sqlite3.Binary` 写 BLOB——写 TEXT 时 Go 端 `value.([]byte)` 断言失败，scan 报 `unexpected end of JSON input`，`is_multi_key` 静默为 false。单 key 渠道也要写 `{"is_multi_key":false,...}` BLOB，NULL 同样触发 scan 错误
- 权重调优（2026-07-28，按 VPS 实测速度配权，谁快谁多拿流量）：
  - opus 池：林夕 w20（3 次采样 2.7s/2.9s/4.7s，稳定快）：百倍 w10（12.7s/12.7s/2.8s，波动大）≈ 2:1，林夕主力、百倍兜底+扩容量
  - `gpt-5.6-sol` 双入口：centos 默认线 ability w10（实测 5.0s）主力，备用线 ability w5 冗余；vyceai 的 sol ability 已于 2026-07-28 晚摘除（见下）
  - vyceai 渠道级从 0/0 修回 50/10（0 权重会导致其独占模型 haiku/sonnet 等完全调度不到）
  - 单源渠道（centos 其余 gpt、grok、vyceai 独占模型）权重不影响调度，保持 10
  - 基准参考：centos gpt-5.5 5.0s / grok-4.5 2.3s（波动大，另一次 10.4s）/ vyceai sonnet-4-6 14.8s
- vyceai 实际挂了 13 个模型（用户在 UI 扩充；`gpt-5.6-sol` 于 2026-07-28 晚摘除——站方直接禁用该模型（`model_disabled`），且 vyceai 不支持 `/v1/responses` 端点（返回 SPA 页面），Codex 请求落上去必 400，NewAPI 对 400 不做故障转移，故删 ability 让 sol 只走 centos）。其余与其他渠道重名的模型（`glm-5.2` 等）NewAPI 按权重在多渠道间随机调度
- vyceai 稳定性提醒（2026-07-28 晚实测）：`glm-5.2`/`mimo-v2.5-pro` 60s 超时、`claude-haiku-4-5` 45.9s——公益站上游波动大，`CHANNEL_TEST_FREQUENCY=30` 会自动摘除持续失败的渠道，无需手动干预
- centos 三线路（2026-07-28 用户提供）：默认 `ai.centos.hk`、备用 `api.centos.hk`、优化 `frapi.centos.hk`，同 key 通用。实测 sol：默认 2.68s/2.95s 最快最稳、备用 5.19s/3.22s、优化 3.00s/4.59s——默认线保持 base_url 不动，备用线建为渠道 #16（ability w5），默认线故障时 NewAPI 自动 failover；`frapi.centos.hk` 留作手工备用未挂
- Kimi Code CLI 已直连本 NewAPI（`http://47.112.162.80:3000/v1`，公网 40ms；弃用 Tailscale 路径 3.2s），客户端模型清单已与实有模型同步
- wintoken-glm（2026-07-28 新增）：capi.cun.ai 被阿里云 IP 段封锁（VPS ping 100% 丢包），同服务的 `www.wintoken.dev` 入口 VPS 直连正常（1.3s），2 key 轮询。glm-5.2 形成双源：wintoken ability w20 主力（实测 chat 4.8s），vyceai ability w10 冗余（当晚上游超时频发）
- sensenova-token（2026-07-28 新增，2026-07-29 更新）：商汤 token plan，VPS 小请求直连很快。`sensenova-u1-fast` 在 `/models` 有列出但调用 404，未挂。`glm-5.2` 的小请求测试曾通过，但 42 万级长上下文在生产中持续触发上游 500 万 TPM 的 `429`，并被 NewAPI 重试放大；现已从 channel 15 models 摘除并禁用对应 ability。Channel 15 继续承载 `sensenova-6.7-flash-lite` 与 `deepseek-v4-flash`，两者摘除 GLM 后复测成功。
- joycode-proxy-jd 渠道已由用户从 NewAPI 删除（JD 账号掉登录 + 风控无法恢复，已放弃）
- ai.168661-grok 直连实测：`/v1/models` 返回 grok-4.3/4.5/chat-fast/imagine-image 四款，按需只挂了 `grok-4.5`；`chat/completions` 实测 HTTP 200（首响约 10s）

## Kimi Code 客户端配置（早间历史方案，已被现代 Kimi Code schema 修正覆盖）

> 本节混用了旧 Python `kimi-cli` 1.48.0 与现代 Kimi Code 的字段，不能继续照抄。日常 `kimi` 当前 provider、thinking、上下文配置及两代 CLI 的版本边界见 [newapi-kimi-mcp-claude-current-state-2026-07-28.md](./newapi-kimi-mcp-claude-current-state-2026-07-28.md)。

`~/.kimi-code/config.toml` 中走本 NewAPI 的别名，以下为早间历史记录（备份 `config.toml.bak.caps20260728`）：

- **claude 别名必须 `protocol = "anthropic"`**：走 OpenAI 格式时思维链全丢；改走原生 `/v1/messages` 后 thinking 块完整返回（实测 blocks: `[thinking, text]`）。7 个 claude 别名均已加
- **`max_output_size` 必填**：kosong CEILING 表无 `claude-opus-5`/`claude-sonnet-5` 版本号，fallback 32000 恰等于 effort=high 的 thinking budget 32000，`budget < max_tokens` 校验必失败 → 之前每次 400。已加 `claude-opus-5 = 128000`、`claude-sonnet-5 = 64000`，实测修复后 200
- **ctx 以上游声明为准**：`glm-5.2` 262144→1048576、`deepseek-v4-flash` 131072→1048576（sensenova 上游声明 1M）
- **thinking 标记按实测填**：`glm-5.2`、`grok-4.5` 实测 reasoning=YES 保留；gpt 全系/deepseek/mimo 实测 reasoning=no 不标；`sensenova-6.7-flash-lite` 不返回 `reasoning_content`（思考混在正文）已去掉 thinking cap

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

当前晚间最终状态见 `docs/ops/newapi-kimi-mcp-claude-current-state-2026-07-28.md`。此前的路由快照、优化方案、补丁记录见 `docs/ops/zg-claude-routing.md`（已标注为历史）。
