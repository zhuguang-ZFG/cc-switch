# BigModel ch207 free-model registration (2026-10-14)

## Context

ch207 `bigmodel-glm53-gift` is the 智谱 BigModel official channel (type 26 = Zhipu V4
adaptor, empty `base_url` → adaptor default `https://open.bigmodel.cn/api/paas/v4`,
gift key in `~/.new-api-local/bigmodel-gift-key.txt`, multi-key size 1, `auto_ban=0`,
manually disabled 2026-10-11). It carried `glm-5.3,glm-5.3-flash` — both paid upstream
(¥8/28 and ¥0.8/2.8 per 1M) — so no free tier was exposed even though the same gift key
reaches it.

## Upstream evidence (direct, gift key, 2026-10-14)

- Catalog `GET /api/paas/v4/models`: 11 ids (glm-4.5 … glm-5.3-flashx), no free id.
- Official pricing page free list (`docs.bigmodel.cn/cn/guide/start/pricing`):
  GLM-4.7-Flash, GLM-4-Flash-250414, GLM-Z1-Flash, GLM-4.6V-Flash, GLM-4V-Flash,
  GLM-4.1V-Thinking-Flash, CogView-3-Flash, CogVideoX-Flash.
- Live chat probes (all `max_tokens` capped; verdicts):

| model | verdict |
|---|---|
| glm-4-flash-250414 | 200 |
| glm-4.7-flash | 200 (reasoning model; eats the completion budget) |
| glm-z1-flash | 200 (reasoning) |
| glm-4v-flash | 200 |
| glm-4.1v-thinking-flash | 200 |
| glm-4.6v-flash | 429 code 1305 twice (congested; id valid) |

- 1305 `该模型当前访问量过大` arrives as **HTTP 429**: inside the fork's retry code
  table, so in-pool retry/OMP failover semantics apply. No fake-success path and no
  `status_code_mapping` needed.
- cogview/cogvideox skipped: image/video endpoints, not on the chat adaptor's surface.

## Change (DB direct; key column untouched)

- `channels.models` ch207 → `glm-5.3,glm-5.3-flash` + the six free ids.
- `options.ModelRatio`: the six ids → 0 (`glm-4.7-flash` was a stale 0.5). All six had
  **zero carriers** in `abilities` (`model LIKE` scan), so this rewrites no shared-id
  billing. `glm-5.3` 0.7 / `glm-5.3-flash` 0.075 untouched; no CompletionRatio and no
  ModelPrice writes (avoids the ModelPrice merge bug).
- `POST /api/channel/fix` → 8 `abilities` rows for ch207, all `enabled=0`.
- Backup: `newapi-before-zai-free-models-20261014-155246.db` (62676992 bytes, quick_check ok).

## Verification

- Read-back: models list + ratios (paid entries unchanged).
- Gateway `POST /v1/chat/completions` model=glm-4-flash-250414 → HTTP 503
  `model_not_found ... No available channel for model glm-4-flash-250414 under group default`
  = expected fail-closed while ch207 is disabled; no half-live leak.
- Gates: `test_omp_routes.py` 40/40 OK; `verify_omp_newapi_alignment.py` verdict CLEAN
  (15 quarantined, unchanged).

## Enable procedure (DONE 2026-10-14, user-approved)

1. **Assert server-side ratios first** (`GET /api/option/` with admin token → `ModelRatio`):
   the six ids must read 0 on the server (re-verified at enable time: all six = 0;
   `glm-5.3`=0.7, `glm-5.3-flash`=0.075). If a stale value appears, `PUT /api/option/` with the
   **full** ModelRatio JSON (single-key option PUT works in this fork; channel full-object PUT
   does not). This avoids silently over-charging quota on free models after enable.
2. ch207 status 2→1 (`POST /api/channel/207/status`). Enabling re-exposes
   `glm-5.3`/`glm-5.3-flash` at p20/w1, which spend the gift card; the card expires and
   BigModel exposes no quota endpoint.
3. `POST /api/channel/fix`, wait the 65–70 s channel-cache window, then gateway-probe
   `glm-4-flash-250414` → expect 200 attributed to ch207.
4. Optional follow-up: register the free ids in OMP `models.yml` (still optional, not done).

### Enable result (2026-10-14)

- Backup: `new-api-before-stepfun-official-20261014-173040.db` (63303680 bytes).
- `status` POST 200 → `fix` POST 200 → 8 abilities rows all `('default', 1, 20, 1)`
  (enabled=1, p20/w1).
- Admin channel test `model=glm-4-flash-250414`: HTTP 200, success=True, time=0.596s.
- After 75s channel-cache window, gateway relay `glm-4-flash-250414`: HTTP 200, usage
  `{prompt:10, completion:3, total:13}`, attributed to **ch207**.
- Read-back: `status=1`, 8 models, priority=20, weight=1, key length 49 (key column untouched).
- Gates: `test_omp_routes.py` 40/40 OK; `verify_omp_newapi_alignment.py` verdict CLEAN
  (86 ids, 0 without an enabled route, 11 quarantined — unchanged).

