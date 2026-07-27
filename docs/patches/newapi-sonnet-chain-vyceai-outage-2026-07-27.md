# Sonnet 链改道：vyceai 硬挂毒化分类器（2026-07-27）

## 现象

Claude Code auto 模式反复报 `claude-sonnet-4-6[1M] is temporarily unavailable`
（分类器失败，所有 Bash 操作被拦）。

## 根因

- Sonnet 链第一棒 `#125 vyceai-claude`（ch_pri 35）硬挂：连接吊死 60s+ /
  Cloudflare 524 / 502 交替，单请求实测卡 **6m31s**。
- 兜底链串行：第一棒「挂起型」失败吃光分类器短超时（十几秒），`#63` kimi
  来不及接手，客户端先报错。兜底防的是快速失败（401/403/429），防不住挂起。
- `auto_ban` 只摘鉴权错误（401/403），超时/524 不触发，死渠赖在链上。

## 改动

| 项 | 前 | 后 |
|----|----|----|
| `#125` models | sonnet+haiku | **仅 haiku**（sonnet 物理剥离） |
| `#133` priority | 15 | **-21**（免费池 39s+ 慢响应/SSL 间歇 reset，不宜挡在 #63 前） |
| Sonnet 链 | `#125`→`#63` | **`#63`kimi(-20)→`#133`(-21)→`#132`GPT(-32)** |

## 关键坑（复发预防）

**此 fork 按 `channels.models` 路由**：`abilities.enabled=0` 不足以摘渠，
`use_channel` 仍会出现该渠道。必须从 `channels.models` CSV 移除 model 并
`podman restart new-api`。

验证：`claude-sonnet-4-6[1M]` 200 / 4.2s / `use_channel=["63"]`；
`claude-opus-5` 200 / 4.6s / 主池不受影响。

## 回退

vyceai 恢复后，把 sonnet models 加回 `#125.models`（ch_pri 35>-20，自动回主渠）：

```sql
UPDATE channels SET models='claude-sonnet-4-6,claude-sonnet-5,claude-haiku-4-5,claude-haiku-4-5-20251001,claude-haiku-4-5[1M],claude-sonnet-4-6[1m],claude-sonnet-4-6[1M]' WHERE id=125;
UPDATE abilities SET enabled=1 WHERE channel_id=125 AND model LIKE 'claude-sonnet%';
-- podman restart new-api
```

DB 备份：`/opt/new-api/data/backups/one-api.before-vyce-sonnet-off-*.db`。
