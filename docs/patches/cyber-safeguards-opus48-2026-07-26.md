# Patch: Cyber Safeguards / Opus 4.8 AUP (2026-07-26)

## Symptom

```
API Error: Opus 4.8 (1M context) can't help with this. Start a new session to continue.
… https://www.anthropic.com/learn/aup
```

Server-side Anthropic policy (Cyber Safeguards), not NewAPI outage.

## Operator actions

1. New session (do not continue poisoned transcript).
2. Leave Opus **4.8**; prefer Opus **5**, Sonnet, or ZG `glm-5.2`.
3. Security-shaped work: swap model first; avoid dumping huge security files into context.
4. Optional: CVP + `/feedback` + `req_…` IDs (community: often ineffective short-term).
5. Long-term cyber-adjacent: non-Anthropic (ZG glm / Composer / Codex).

## References

- https://github.com/anthropics/claude-code/issues/60366
- https://github.com/anthropics/claude-code/issues/50916
- https://github.com/anthropics/claude-code/issues/61889
- https://support.claude.com/en/articles/15363606
- https://yurukusa.github.io/cc-safe-setup/claude-code-cyber-safeguard-false-positive.html
- `npx cc-safe-setup` — advisory hooks only

## Docs updated

- `docs/ops/newapi-dx-cursor-ops.md`
- `docs/ops/zg-claude-routing.md`
