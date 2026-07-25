# NewAPI AgentRouter pin / restore (2026-07-26)

## Timeline

1. **Pin (empty token):** Failover hit AR with  
   `token remain quota ≈ $0.02, need ≈ $0.66` → client 403.  
   Applied `status=2` on `#118–120`.

2. **Top-up:** Operator recharged AR API tokens (account/token remain fixed).

3. **Restore (this note):**
   | Channel | State |
   |---------|--------|
   | `#118` | **Live** `status=1` pri30/**w6**, Opus/Fable abilities on（次池） |
   | `#119` `#120` | **Still status=2** — anti-stall（慢/尾延迟），不是额度 |

Opus 主池仍是 `#9/#10/#20/#60`；`#118` 仅作 NewAPI 内次选。  
本机 FQ `#2` = `agentrouter-2` 直连不变。

## Re-enable 119/120 later

When latency looks good: enable status + Opus abilities at w1, watch soft journal / p50.
