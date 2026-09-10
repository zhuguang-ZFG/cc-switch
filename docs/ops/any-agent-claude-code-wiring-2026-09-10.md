# any/agent 渠道 Claude 模型接入 Claude Code CLI（2026-09-10 晚）

## 1. 需求与结论

用户需求：把 any 渠道（anyrouter，经 8789 桥）与 agent 渠道（agentrouter）的 Claude 模型配置进 Claude Code CLI。

结论：**聚合池早已登记这两条 Claude 路，缺的只是 Claude Code CLI 到 NewAPI 3002 的接线**。

- any-Claude = ch72 `anyrouter`（type 14 → `127.0.0.1:8789` 桥，p40/w5，test_model=claude-opus-5，Guardian `DEGRADED_ACCEPTED_DISABLED[72]` 治理：上游 429 期间保持禁用、恢复后自动重启入池）。
- agent-Claude = ch86 `agentrouter-claude`（type 14 直连 `ps.air-outer.com`，p50/w≤13 垫底备份档）+ ch45 `agentrouter`（8788 桥；2026-08-14 起 Claude 迁出、只留 Sol——隔离过载 Sol 恢复探针对 Claude 容量的连坐）。
- 另有 ch116 `kktoken`（p50/w1，当前唯一启用）、ch123 `zzzcoding`（zz_gate 池窗门控）。
- Claude Code CLI 此前指向 cc-switch 代理 `127.0.0.1:15721`（PROXY_MANAGED），该链当晚 405（与 codex 侧同症状），CLI 实际不可用。

本次**只动 `~/.claude/settings.json`**，不动任何渠道状态：ch72/ch86 当前均为上游侧降级，Guardian 已按既有治理自动恢复，手工启用只会造成"3 探失败→auto-ban→恢复队列"空转。

## 2. 变更

`~/.claude/settings.json`（备份 `settings.json.bak.pre-any-agent-3002-20260910-214529`）：

```json
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "sk-<token#4 claude-max-local，unlimited，grp=default>",
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:3002",
    "ANTHROPIC_MODEL": "claude-opus-5",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-5",
    "ANTHROPIC_SMALL_FAST_MODEL": "claude-haiku-4-5-20251001",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-4-5-20251001"
  }
}
```

要点：

- token 选用 #4 `claude-max-local`（unlimited、grp=default，本就是为 Claude 本地使用建的；不复用 #6 local-windows-clients）。
- `ANTHROPIC_MODEL` 显式钉住池内旗舰名 `claude-opus-5`（ch116/ch72/ch86/ch123 全部登记该名），避免 CLI 默认模型名不在池内吃 503；后台小模型走 `claude-haiku-4-5-20251001`（ch68/69 agnes 替身，见 §3）。
- 覆盖风险：cc-switch（当晚 PID 18152 运行中）下次切换 claude provider 时会重写 settings.json；恢复 = 重放本节 env 或从备份合并。

## 3. 验证（当晚 21:40–21:55）

| 路径 | 结果 | 证据 |
|---|---|---|
| 3002 `/v1/messages` claude-haiku-4-5-20251001（直探） | 200 / 0.8s | ch68/69 agnes 替身（映射 agnes-2.0-flash），claude→openai 转换正常 |
| **真实 CLI 端到端 haiku**：`claude -p "Reply with the single word OK" --model claude-haiku-4-5-20251001` | **OK / 8.7s** | 完整 agentic 流（流式 + 工具）走 3002 全通 |
| 3002 `/v1/messages` claude-opus-5（直探） | 503 | **路由正确**：guardian file-tail 捕获本次请求命中 channel #116，上游 kktoken 自身返回 503 `No available channel for model claude-opus-5 under group default`（kktoken 是 NewAPI 系转售商，报错文本同源冒泡） |
| 真实 CLI opus-5 同探针 | 挂起重试后终止 | NewAPI 503 → CLI 5xx 退避重试，无可用上游时表现为长时间无响应 |
| ch72 any 桥（8789）上游 | 全天 429 | 当日 429×168（08:06–21:24 本地），`load-cap squeeze 1/8…8` 后 500；与 §any-gpt-cutover 记录的 gpt-6-astra 风暴同源同日 |
| ch45/86 agentrouter 上游 | 预算池 402 | 21:40 实测 claude-opus-5：4 把 key 全部 `402 Budget pool quota has been exhausted → key cooled`；ch86 直连域同池，同命运 |
| ch123 zzzcoding | 池窗门控 | `DEGRADED_ACCEPTED_DISABLED[123]`：池空 status=2，zz_gate 自动开合 |

### 当晚 claude-opus-5 可用性矩阵

| 来源 | 渠道 | 状态 | 卡点 |
|---|---|---|---|
| any（8789 桥） | ch72 | 禁用（接受态） | 上游全天 load-cap 429 |
| kktoken | ch116 | 启用 w1 | 上游池空 503（09-06 起每日 soft failure） |
| agentrouter | ch86 直连 / ch45 已迁出 | 禁用 / 仅Sol | 预算池 402（4 key 全冷却） |
| zzzcoding | ch123 | 禁用（池窗） | 池空自动门控 |

**接线本身已验证可用**（haiku 端到端 200）；opus-5 无一上游健康属当晚上游故障潮，非接线问题。

## 4. 恢复路径（无需人工操作）

- ch72：Guardian 有界退避持续探测 claude-opus-5，上游 429 窗口结束后自动重启入池（08-09 起的同款治理）。
- ch45/86：agentrouter 预算池属上游账号侧额度，恢复后 Guardian 探测通过自动启用；ch86 受 `OPUS_BACKUP_CAPS` 约束（p≤50/w≤13 垫底）。
- ch123：zz_gate 池窗自动开合。
- 上游恢复后 Claude Code **无需任何改动**——同名模型在 3002 聚合池内自动获得 any/agent/zz/kk 多源 failover（priority 降序选路）。

## 5. 回滚

```bash
cp ~/.claude/settings.json.bak.pre-any-agent-3002-20260910-214529 ~/.claude/settings.json
```

回到 cc-switch 15721/PROXY_MANAGED（注意该链当晚 405）。
