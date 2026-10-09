# NewAPI 定制代码深度评审（2026-07-27）

> **修复状态（2026-07-27 当日）**：P1-1~10 已全部修复并部署（commit `d60cf80`，guard 滚动重启 + 冒烟通过）。
> P2/P3 已修并部署（同日第二批）：P2-12/15/16/17/18/19/20、P3 全部（kiro_guard 死代码/快照 clamp/PROXY 脱敏/Content-Type/Content-Length+max_tokens 兜底/TG 告警异步化/deque 滑窗；dx pct off-by-one/smoke token 优选/SHORT_IN_BOUNDS/备份留 10 份；health_check 探活加 code==200、_secret 改 with、state 原子写+复活清残留；sonnet_failover 默认 probe-only + 双挂 TG 告警，`--force` 才写 DB）。
> **未动项已清零（同日第三批 tee 改造）**：P2-11（`KIRO_GUARD_MAX_ACTIVE_REQUESTS`=16 快速 503）、P2-13（tee 路径透传真实状态码；缓冲慢路径细分 authentication/permission error）、P2-14（cyrillic decode 上下文正则）、P2-12 残余（缓冲路径 acc_msg 累积式 continuation）全部随 tee 模式落地并部署，详见 `docs/patches/kiro-guard-tee-mode-2026-07-27.md`。tee 默认开（`KIRO_GUARD_TEE=1`），实测 TTFB 3.4s（原 ~14s）。
> 部署后冒烟新发现并已处理：#129 上游（Dc公益 8317）auth 池耗尽导致 Opus 兜底 503 → 新增 **#130 gpt-123458-claude-fallback**（克隆 #124，ch_pri=-31）作为第二独立兜底，Opus 恢复 200；health_check 多 key 渠道（\n 分隔）拼出非法 Bearer 头 → probe 取首个 key（已随本批部署）。
> dx cooldown（P1-5）与 health_check alerted（P1-8）待下一个 cron 周期自然验证。

**范围**：`scripts/ops/` 下 VPS 镜像脚本（kiro_guard / analyze_newapi_dx / health_check / sonnet_failover）。cc-switch 不在范围。
**方法**：3 个评审代理全文通读 + 关键 finding 人工复核。

## 复核修正

- ❌ ~~`apply_soft_limit` 追 Bash 块后错设 `end_turn`~~（kiro_guard:909-935）——**误报**。`end_turn` 只在 text-notice 分支（:929）；Bash 分支落到 :934 正确设 `tool_use`。
- ✅ 已人工确证：worker 无异常兜底（:1246）、内容审查 marker 在 200 响应上生效（:1058 在状态判断前调用）、超时预算不自洽（TIMEOUT=300×(1+SOFT_RETRY=1)≈600s vs handler 300s）、dx cooldown JSON 键类型失配（:663/:683）。

## P1（真实 bug，建议本周修）

| # | 位置 | 问题 | 修法 |
|---|------|------|------|
| 1 | kiro_guard:1246 | worker 线程无 try/except，任何内部异常 → `put` 永不发生 → 客户端白挂 300s | worker 包 except → put 结构化 502 |
| 2 | kiro_guard:386/1381/1424 | `except HTTPError` 内 `_read_limited(e)` 遇 >10MB 错误体再抛 ValueError，无人接 | 三处包 try→`b""` |
| 3 | kiro_guard:1058/1126/1429 | 内容审查 marker 不区分状态码，**200 正常回答**含「敏感词/内容审核/content filter」等词会被误判 content_blocked → 502 + 切渠 | 仅对非 2xx 错误体扫描 |
| 4 | kiro_guard:1311 vs 1098 | 超时预算不自洽：handler 等 300s，`fetch_classified` 最坏 600s → 重试未完成就被判 502，白烧上游 token 还误切渠 | handler 等待上限 = (1+SOFT_RETRY)×TIMEOUT+退避，或传总 deadline |
| 5 | analyze_dx:663/683 | cooldown 永不生效：`last_suggested` int 键经 JSON round-trip 变 str 键，比较恒 False → 每次跑都写库+重启（当前日 cron 未炸，改高频即炸） | 比较前键转 int 或存排序 list |
| 6 | analyze_dx:685 | `--dry-run` 仍执行真实付费 smoke（4 个模型请求烧用户额度） | dry_run 时跳过 smoke |
| 7 | analyze_dx:152 | `use_time` 单位启发式（<1000 当秒）会把 ≥1000s 卡死请求误判为 1s → p50/p90 失真，stall 渠道逃逸 | 按 schema 固定 ×1000，去掉启发式 |
| 8 | health_check:365-368 | 禁用 API 失败后 TG 告警**每 30 分钟无限重发**刷屏 | 加 alerted 标记，成功后清除 |
| 9 | sonnet_failover:83-87 | 探活不查 `channels.status`，可把已禁用渠道提为「假主渠」 | probe 前查 status≠1 判 down |
| 10 | health_check:219/248 | transient 判定只用 `out[:300]`，长错误体里 `no available accounts` 落在截断点外 → 公益站翻浆被误计硬失败，6h 后误禁渠道 | 判定用完整 body 或放宽到 2KB |

## P2（隐患，排期修）

| # | 位置 | 问题 |
|---|------|------|
| 11 | kiro_guard:1248/1848 | 线程数无上限（每连接 2 线程、存活最长 600s），上游挂死时线性堆积；backlog=5 满后 RST |
| 12 | kiro_guard:1097-1139 | SOFT_RETRY≥2 时 continuation 基准与 merge 基准不一致，中间段丢失（默认 =1 不触发） |
| 13 | kiro_guard:1258-1303 | 慢路径（>4s）下 401/403 也以 200+SSE error 呈现，NewAPI 可能不判死坏渠道 |
| 14 | kiro_guard:943-984 | cyrillic decode 无差别替换，正当俄语内容落盘损坏（仅 AR 实例，文档已警示勿开到他渠） |
| 15 | analyze_dx:66-68 | `sh()` 不查 rc 不捕超时：journalctl 失败静默变「证据不足」，podman restart 失败照报成功 |
| 16 | analyze_dx:347-349 | `UPDATE abilities` 全量覆写该渠道所有行 priority（差异化设置被抹平）；无 flock，并发双写 |
| 17 | analyze_dx:418 | env 变更时同时 restart 全部 5 个 guard，在途请求全掐；应滚动 |
| 18 | analyze_dx:275 | 运维手动置 0 的权重会被自动「复活」（denylist 只挡 11/81） |
| 19 | health_check:89-91 | state 文件非原子写，崩溃即计数清零；复活后 fail 计数不清零 → 一次失败立即再禁 |
| 20 | sonnet_failover:120-123 | 双渠道全挂只 print return 1，**零告警**（auto-mode 分类器整体挂掉无人知晓）；单发探针无复探 |

## P3（顺手修）

- kiro_guard:709 `mark_incomplete` 死代码；:240 snapshot interval=0 变忙循环；:1482 `/metrics` 回显 PROXY（可能含凭据）；:1020 可能缺 Content-Type；:1229 Content-Length 解析无兜底；:276 TG 告警同步阻塞响应路径最长 10s
- analyze_dx:71 pct() off-by-one；无界增长（reports/backups/env.bak 无清理）；smoke 固定用第一个 token（额度耗尽即假故障）；SHORT_IN_BOUNDS 死常量
- health_check:215 探活成功判定只看 body 子串不看 HTTP code；OPUS_POOL value 未使用
- sonnet_failover:74 错误摘要只剩 body 最后一行（pretty JSON 只剩 `}`）

## 总体评价（三处最薄弱）

1. **guard 的出错路径**（P1-1/2/4 同源）：worker 无兜底 + 超时预算错误——这套 guard 存在的意义就是处理出错，而它自己的出错路径最脆。
2. **证据采集不可信，自动闭环建在沙子上**（P1-7、P2-15/16）：dx 的证据层（journalctl/日志/sh）失败全部静默降级，下游「智能决策」照跑且无告警出口。
3. **探针语义与 NewAPI 路由状态脱节**（P1-9/10、P2-20）：两个探活脚本直连上游裸探，不看 channel status/abilities，结论与「流量实际走不走得通」有系统性 gap。另注：sonnet_failover 调的是 abilities.priority，而今天已实证**路由主排序键是 channels.priority**——该脚本的对调动作本来就大概率无效，建议直接废弃或改写为调 channels.priority。

## 建议修复顺序

1. guard 三件套（P1-1/2/4）——直接影响线上稳定性，改动小
2. P1-3（内容审查误杀）——一行状态码判断
3. dx 两件（P1-5/6）+ sonnet_failover 废弃/改写（P1-9 + 路由键事实）
4. 其余 P2 随 tee 模式改造一起做（P1-4 超时预算、P2-11/13 与 tee 重写同区域）

---

## 后续修复（2026-07-28）

### unified_router.py 参数绑定 bug（P0，阻断 opus_fallback tier）

**症状**：unified_router cron 每 5 分钟在 `opus_fallback` tier crash（`sqlite3.ProgrammingError: Incorrect number of bindings supplied. The current statement uses 8, and the 7 supplied.`）。

**根因**：`process_tier()` L319 用 config 全量 `channel_ids` 生成占位符 `ph`（6 个 `?`），但后续所有 SQL 用过滤后的 `ids`（`status=1` 过滤后可能只剩 5 个）。`ph` 与 `ids` 数量不匹配。

**修复**：在 `ids = [r[0] for r in rows]` 之后重建 `ph = ",".join("?" * len(ids))`。

**TWINS**：searched `% ph` pattern — found 4 sites: L201/L223 用各自函数参数 `ids` 生成（正确）；L322 用 config `channel_ids` 配合全量参数（正确）；L343 是唯一 bug 点，已修。本地 + VPS 同步修复。

### 渠道配置修复

| 修复 | 说明 |
|------|------|
| #138 kimi-k3 权重 25→5 | k3 国产模型冒充 opus-4-8，不应高权重。affinity 关闭后不再被钉死，但权重仍不合理 |
| #140 muyuan 手动恢复 status 3→1 | 被 auto_ban 但此刻公益池可能已自愈 |
| admin 额度设无限 | quota=999999999999，所有 admin tokens unlimited_quota=1 |

### 渠道可用性快照（2026-07-28 ~14:00）

| 渠道 | 状态 | 备注 |
|------|------|------|
| baibei 5 key (#9/#10/#20/#81/#143) | ✅ 全在线 | 真Claude主力，权重最高 |
| 林夕 #11/#60 | ⚠️ 间歇超时 | k40.shengqainbang.cn 此刻超时 15s |
| vyceai #125/#126 | ❌ 全挂 502 | 上游 vyceai.com 502/超时 |
| welfare.0xpsyche #142 | ❌ POST 403 | key 有效（models 200）但 chat/completions 被 WAF 拦 |
| muyuan 8317 #140/#129 | ⚠️ 间歇 | 公益池 auth 耗尽会自愈 |
| FreeModel #131/#132/#133/#135 | ❌ 全禁用 | WorkBuddy 客户端验证，API 直连 403 |

### unified_router dry-run 验证

修复后 dry-run 完整跑通两个 tier，决策合理：
- **opus_main**: #60 林夕最快（2.7s→w42），#9 baibei（3.8s→w23），#10（3.4s→w22）
- **opus_fallback**: #96 joycode（0.4s→w25），#142 wuming（1.3s→w25），#139 grok（3.4s→w8）
