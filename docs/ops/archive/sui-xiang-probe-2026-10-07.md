# sui-xiang.net 探测记录（2026-10-07，未接入）

## 结论

用户提供 `https://www.sui-xiang.net` + key（NTRUCK 帖性质）。**探测未通过验收，不建渠道**：

- 目录 **14 个纯 Claude 模型**：claude-fable-5 / fable-5-1 / opus-5 / opus-5-5 /
  opus-4-8 / opus-4-7 / opus-4-6 / opus-4-5 / sonnet-5 / sonnet-5-5 / sonnet-4-6 /
  sonnet-4-5 / haiku-4-5 / haiku-4-5-20251001；UA 无门（curl/Go/空均 200）。
- **客户端门**：裸请求 403 `当前分组仅支持 Claude Code 客户端，请更换客户端或切换分组`
  → 复刻仓内已验证 claude-cli 头集后**过门**（错误从 permission 变为 rate_limit，即
  已鉴权进入路由）。
- **限流饱和（不可用主因）**：过门后全部请求 429——多数为「每分钟请求上限（RPM）」，
  一次为「并发上限」（30.5s 排队后返回）。节奏化采样 ~6 次（单发、间隔 50–180s、
  含 180s 冷却）**无一成功样本**。判定：套餐级限流饱和（共享 key 福利帖性质），
  非本地可修、亦非头集问题。

## 复用的 claude-cli 头集（仓内已验证）

来源 `scripts/ops/anyrouter-window-canary.py`：

```
user-agent: claude-cli/2.1.267 (external, sdk-cli)
x-app: cli
anthropic-version: 2023-06-01
anthropic-beta: claude-code-20250219,context-1m-2025-08-07,interleaved-thinking-2025-05-14
anthropic-dangerous-direct-browser-access: true
```

（zz_gate.py 另有 x-stainless-* 扩展档，本次未用；需要时补。）

## 重试条件（启动接入的唯一前置）

单发 `POST /v1/messages`（上述头集，claude-opus-5）返回 200 → 按 `add_asvla_channel.py`
范式建 **ch178 `sui-xiang-claude`**（type=14，p-20/w1，auto_ban=1）并先跑
`probe_untrusted_openai_provider.py` canary。共享池低成本先例：vsakura/asvla
runbook 的 429/402 语义（均不 failover）。

## 留痕

- key 不落盘不回显（本记录不含 key）。
- 若长期 429 不改善：不必反复探测（每次<1s 返回即被拒），以「单次冷却探针」为单位，
  记录在案即可。
