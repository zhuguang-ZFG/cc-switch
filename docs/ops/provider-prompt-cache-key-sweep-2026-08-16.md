# Direct provider health sweep — prompt_cache_key matrix (2026-08-16)

Trigger: OMP 400 from runinfra (`hosted_parameter_not_supported:
prompt_cache_key`). Swept every **direct** provider in
`~/.omp/agent/models.yml` with the OMP wire shape (request carrying
`prompt_cache_key`) to find the same defect class elsewhere.

| Provider | Result | Action |
|---|---|---|
| runinfra | 400 hard reject | routed via NewAPI ch88 + param_override delete (see `runinfra-qwen-via-newapi-2026-08-16.md`) |
| ooioo | 200, field tolerated | none |
| longcat | 200, field tolerated | none |
| mistral-official | 200, field tolerated | none |
| fengwind | **502 with or without the field** | separate outage, not the cache-key defect |

## fengwind 502 (2026-08-16, 21:3x window)

Impact while down: OMP `fallbackChains` entries pointing at fengwind
(deepseek-v4 chains, smol chain, `zai-glm-5-2`) fail through to the next
candidate — controlled degradation, no silent drop. Follow-up (same day,
glm5.2 review): `zg-newapi/zai-glm-5-2` chain now leads with
`mistral-official/glm-5-2` ahead of the dead fengwind entry. No chains
removed; prune fengwind entries only if the outage is confirmed permanent.
Corroborating: NewAPI ch20 `fengwind-gpt56sol` had already been
auto-disabled by Guardian (status=2) in the same window.

## Verification-shape rule (lesson)

A provider is not "verified" for OMP by a plain curl. The request must match
OMP's wire shape: `prompt_cache_key`, `stream_options`, `enable_thinking`
(for reasoning models), `max_completion_tokens`, stream. OMP injects
`prompt_cache_key` unconditionally (no per-provider suppressor in the
models.yml schema) — see the runinfra runbook for the source-level
analysis.
