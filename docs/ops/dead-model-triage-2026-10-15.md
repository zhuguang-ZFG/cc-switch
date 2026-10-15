# 死模型大扫除（2026-10-15）

用户报「其他模型几乎都用不了」。系统性排查：ch220 lucky0625 被 auto_ban
（账户负额）引发连锁死亡 + agnes 账号受限波 + ch109/111 上游移除/欠费。
本 runbook 记录归因与修复。

## 归因（全部上游真死，无腿可捞）

| 渠道 | admin test 证据（禁用状态下测） | 判定 |
|---|---|---|
| ch220 lucky0625 | 403 `用户额度不足，剩余额度 $-0.000278`（全模型同文，账号级） | 福利钱包打空，auto_ban 正确 |
| ch69 agnes-cn-haiku | 403 `账号异常活动，权限暂时受限` | 账号级封禁 |
| ch68 agnes-com-haiku | `do request failed` | 上游死 |
| ch109 imagic | 400 `Model is unavailable`（mimo） | 模型被计划移除；渠道 auto_ban |
| ch111 bai-free | 400 `credit insufficient balance=0` | 钱包空 |

连带死亡（alignment 孤儿 12）：glm-5.2、gemini-3.8-flash（均 ch220 独腿）、
agnes-2.5-flash、claude-haiku-4-5、LongCat-2.0（ch68/69）、mimo-v2.5、
grok 全家×6、muse-spark-1.2-contributor（ch109/111）。

用户体感「几乎都用不了」的直接原因：OMP 角色 plan/designer/task=glm-5.2、
tiny=gemini-3.8-flash（全灭），smol/commit 链含 mimo/haiku 死引用每轮白烧
一次回退；default 用户切到 dsv41f 又撞 ch15 计划墙（16:39 已修）。

## 修复

**路由（config.yml，备份 `.bak-20261015-deadmodel-cleanup`）**
- `plan` / `designer` / `task`：`glm-5.2:max` → `k3:max`（ch33 活体确认
  200 归因 + reasoning budget 生效；1M ctx、efforts 满档）；
- `slow` 链：`glm-5.2:max` → `k3:max`；
- `tiny`：`gemini-3.8-flash` → `glm-5.3-flash`（3 腿池）；tiny 链去
  gemini → `[deepseek-v4-flash, qwen3-8-27b]`；
- 链清尸：smol 去 `mimo-v2.5`，commit 去 `claude-haiku-4-5`，删
  `zg-newapi/agnes-2.5-flash → [mimo-v2.5]` 死链条目（全链皆尸）。
- dsv41f 前修（同日下午，见 dsv41f runbook 补记）：ch15 摘除该名
  （sensenova 计划外 403 墙，single-key 库直写）+ ch218 p0→p30 主腿；
  relay 200 归因 **ch218**（opencode 免费腿，1M ctx/200K 输出/五档）。

**隔离注册表（verify_omp_newapi_alignment.py QUARANTINE）**
新增/更新 13 条死因条目（glm-5.2、gemini-3.8-flash、agnes-2.5-flash、
claude-haiku-4-5、mimo-v2.5、grok×6、muse-contributor、LongCat-2.0），
verdict 12 → 1（存量 Atria）。

## 门禁

- `test_omp_routes` 40/40 OK（角色重指+链清理后）。
- alignment verdict 1 orphan（基线）。
- models.yml `glm-5.2` 条目注记改为「全腿灭，角色已迁 k3」。

## 遗留

1. **ch220 回血观察**：lucky0625 钱包回血后可回开（glm-5.2/gemini-3.8-flash/
   agnes 复活，回指与否看当时角色状态）。
2. **agnes 账号受限**：官方解封后 ch69 可测活再开；ch68 上游恢复同理。
3. **OMP 需重启**生效本轮 models.yml/config.yml 改动（上一轮重启后又累积
   了 dsv41f 能力值与本轮角色改动）。
4. grok 全家/mimo-v2.5/muse-contributor：需新腿才能复活，暂无来源。
5. ch118 seeseed 1h 内 84× 530（Cloudflare 源不可达抖动），池已吸收
   （glm-5.3-flash 经 ch173 兜底 28 成功）——持续恶化再处理。

## 回滚

```bash
cp ~/.omp/agent/config.yml.bak-20261015-deadmodel-cleanup ~/.omp/agent/config.yml
cp ~/.omp/agent/models.yml.bak-20261015-deadmodel-cleanup ~/.omp/agent/models.yml
# NewAPI dsv41f 路由：cp backups/new-api-before-dsv41f-reroute-20261015-163928.db
```
