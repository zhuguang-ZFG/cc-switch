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

## 4. 关联发现 + 处理：gpt-5.6-sol 两个渠道已死

修复亲和后发现 gpt-5.6-sol 失败率高，根因是渠道健康而非亲和。近 40 次请求统计：

| 渠道 | 名称 | ok | fail | 状态 |
|---|---|---|---|---|
| ch16 | centos-api-backup-gpt | 0 | 8 | 全死（status 仍=1，auto_ban 未触发） |
| ch30 | fastaitoken-gpt | 0 | 19 | 全死（同上） |
| ch34 | 4router-gpt | 10 | 0 | 健康 |

且原配置 ch16/30/34 同优先级 50、按权重分流，ch30 权重最高（20）反而分到最多流量（成功率仅 30%）。

**处理（按用户选择：降权保留兜底）**：把 ch16/30 的 gpt-5.6-sol ability 降为 `priority=10, weight=5`（兜底），ch34 保持 `priority=50`（主渠道）。热改 abilities 整数列（MEMORY_CACHE 自动同步，无需重启）。改前备份 `one-api.before-gptsol-weight-<ts>.db`。

效果：gpt-5.6-sol 默认走健康 ch34（近窗 7/7 成功），缓存局部性由优先级保证。残留偶发失败来自 ch34 瞬时抖动时兜底渠道（ch16/30）亦死——保留死渠道作兜底的固有取舍；若 ch16/30 上游恢复需手动调回优先级（手动降级不被 auto_ban 自动恢复）。

> 安全：本文档不含 NewAPI admin token、VPS 密码、用户 token。
