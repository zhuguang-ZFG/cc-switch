# NewAPI 渠道亲和性修复（OMP 非 claude 模型）（2026-07-31）

排查 prompt cache 命中率时发现：NewAPI 的渠道亲和性（`channel_affinity_setting`）虽已启用，但对 OMP/Kimi 走的非 claude 模型**实际不生效**——同一 token 的请求被打散到多个上游渠道，prompt cache 永远命不中。本次修复亲和 key 源。

## 1. 诊断

`channel_affinity_setting.enabled=true`，有 7 条规则。但：

- claude 规则 key 源是 `metadata.user_id` + `User-Agent` header（总是存在）→ 亲和有效。
- gpt/glm/grok/qwen/deepseek/longcat 规则 key 源全是请求体的 `prompt_cache_key` 字段。OMP/Kimi 作为通用 OpenAI 客户端**不发这个字段** → 亲和 key 为空 → 不粘滞。
- `glm trace` 规则被显式 `enabled=false` 关闭。

铁证（logs 表，修复前同一 token=3 的 gpt-5.6-sol 渠道分布）：

```text
ch2(650) ch16(343) ch25(143) ch12(98) ch30(67) ch34(18) ch23 ch145  —— 打散到 8 个渠道
```

## 2. 修复

给 6 条规则（codex/glm/grok/deepseek/longcat/qwen trace）的 `key_sources` 追加 `{"type":"request_header","path":"User-Agent"}` 作 fallback（对齐 claude 规则做法），并启用 `glm trace`（`enabled=true`）。

客户端不发 `prompt_cache_key` 时，亲和 key 退化为 User-Agent，OMP 流量按客户端粘滞到一个渠道。单用户场景下按客户端粘滞是合适粒度。

改前备份：`/opt/new-api/data/backups/one-api.before-affinity-20260731-<ts>.db`。经 sqlite 直写 options（停容器写入，启动后 MEMORY_CACHE 重载）。

## 3. 验证

```text
glm-5.2（glm trace 启用后）  -> 连续 4 次全粘滞 ch14（STICKY）
gpt-5.6-sol                  -> 亲和生效，请求集中到少数渠道（不再打散到 8 个）
```

## 4. 关联发现：gpt-5.6-sol 两个渠道已死（独立问题）

修复亲和后发现 gpt-5.6-sol 失败率高，根因是渠道健康而非亲和。近 40 次请求统计：

| 渠道 | 名称 | ok | fail | 状态 |
|---|---|---|---|---|
| ch16 | centos-api-backup-gpt | 0 | 8 | 全死（status 仍=1，auto_ban 未触发） |
| ch30 | fastaitoken-gpt | 0 | 19 | 全死（同上） |
| ch34 | 4router-gpt | 10 | 0 | 健康 |

ch16/30 成功率 0% 但仍启用，约 2/3 请求先撞死渠道失败再 failover 到 ch34。需禁用这两个渠道的 gpt-5.6-sol ability（或排查上游）。此为独立于亲和的渠道健康问题。

> 安全：本文档不含 NewAPI admin token、VPS 密码、用户 token。
