# OMP models.yml hot-reload fallback incident (2026-08-16)

## Symptom

Mid-session, the active model `zg-newapi/k3` silently downgraded to
`zg-newapi/claude-haiku-4-5` for ~88 seconds (5 main-thread turns, including a
final delivery), then reverted to k3 on its own.

## Root cause (evidence-backed)

Timeline from session transcript
(`~/.omp/agent/sessions/…/2026-08-16T12-45-39-745Z_….jsonl`) and NewAPI logs:

| Time | Event |
|---|---|
| 20:48:51.765 | Last k3 request completed OK (NewAPI: ch33, stream ok) — **no upstream failure** |
| 20:48:51.813 | `~/.omp/agent/models.yml` rewritten (provider append via edit tool) |
| 20:48:51.884 | `model_change → claude-haiku-4-5, role:"fallback"` — 71 ms after the write |
| 20:48:56–20:49:38 | 5 turns on haiku (auto-compaction log shows `contextWindow:200000`; k3 is 1M) |
| 20:50:20 | auto-revert to k3, `role:"default"` — matches `fallbackRevertPolicy: cooldown-expiry` (~90 s) |

Mechanism: the models.yml hot-reload transiently unresolves the active model
selector; with `retry.modelFallback: true` and **no fallback chain defined for
k3**, OMP's retry-fallback picked claude-haiku-4-5. The trigger was a live
models.yml edit while a session was running.

Diagnostic anchors (reuse for the next incident):

- session jsonl `model_change` events — `role` field distinguishes
  `default`/`fallback` assignments
- auto-compaction log `contextWindow` — 200K = haiku, 1M = k3
- NewAPI consume log — proves whether the primary model actually failed
  upstream (here it did not)

## Fix applied

`~/.omp/agent/config.yml` `retry.fallbackChains` gained:

```yaml
zg-newapi/k3:
  - zg-newapi/deepseek-v4-pro
  - zg-newapi/deepseek-v4-flash
```

Any future transient unresolve (or genuine k3 upstream error) now degrades to
deepseek-v4-pro instead of haiku. Fallback-chain changes are cached at
startup — **restart the OMP session for them to take effect**.

## Operational rules

1. Do not edit `~/.omp/agent/models.yml` while OMP sessions are running; if a
   live edit is unavoidable, expect a ~90 s fallback window.
2. ~/.omp is not a git repo (models.yml/config.yml untracked) — concurrent OMP
   sessions rewrite config.yml; check mtime before and after any edit.
3. Do not patch the globally installed OMP package; route fixes through
   config (fallback chains) or process discipline.
