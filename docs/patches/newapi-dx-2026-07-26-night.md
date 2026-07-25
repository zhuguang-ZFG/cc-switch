# NewAPI / cc-switch DX — 2026-07-26 night

Ops-only pass (VPS NewAPI + local cc-switch). No application code change required for daily use.

## Goals

- Stabilize Opus path through ZG + kiro-guard
- Put AgentRouter behind guard; keyword → failover
- Reduce nested retries / false quota disables
- Align local Claude entry with Opus-first

## Live topology (after)

```
Claude Code → cc-switch :15721 (ZG current)
  → https://aliyun.donglicao.com
      Opus 主池: #9/#10/#20 (pri45, w50/40/32) + #60 (w8)
                 #11 pinned status=2 (AUTO_REACTIVATE_EXCLUDE)
                 #81 Opus/Fable stripped (restart new-api after strip)
      Opus 次池: #118–120 → kiro-guard-ar :8410–8412
                 (PROXY=7890, CYRILLIC_BYPASS=1, CONTENT_BLOCK→502)
      Sonnet: glm-5.2 → zhipu; Haiku: Agnes/LongCat
Local FQ: ZG → agentrouter-2 (林夕 removed)
Proxy: first_byte=25s, max_retries=2
NewAPI: RetryTimes=3
```

## Changes

| Area | Change |
|------|--------|
| kiro-guard | Soft-trunc + 700ms backoff; empty tool; SOFT_LIMIT; content_block→502; AR Cyrillic-Bypass |
| AR channels | `#118–120` base=`127.0.0.1:841x`; setting.proxy cleared |
| Failover | RetryTimes 5→3; `#11` pin + SKIP-REENABLE; `#60` w8; health quota tighten |
| Weights | DX analyze: `#9/#10/#20` → 50/40/32 (backup `one-api.before-dx-weights-20260726-021915.db`) |
| Local cc-switch | `ANTHROPIC_MODEL=claude-opus-5[1M]`; FQ drop 林夕; `max_retries=2` |

## Verification

- Guard selftest + AR metrics `cyrillic_bypass=true`
- Gateway / local proxy smokes: Opus / Sonnet alias→glm / Haiku OK
- Pin verify: 3× Opus → `#20`/`#10`, zero hits on `#11`/`#81` after restart
- DX analyze smoke_ok after weight apply

## Docs

- `docs/ops/zg-claude-routing.md`
- `docs/ops/newapi-dx-cursor-ops.md`
- Run: `python scripts/ops/newapi-dx-analyze.py`

## Do not

- Bounce `/api/channel/:id/status` to “fix” abilities (re-enables all)
- Re-enable `#11` until k40 stable
- Tighten `first_byte` below 25s without community contract
- Commit `D:\Downloads\VPS.txt` or `.tmp-*` probe scripts
