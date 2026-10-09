# Mistral glm-5-2 channel via local conversations relay (2026-08-16)

## Scope

A Mistral API key (32-char, account serves 55 models including `glm-5-2` and
`zai-glm-5-2`) was installed as NewAPI channel **ch85** `mistral-zai-glm-5-2`.
The upstream serves GLM models **only** via Mistral's proprietary
`/v1/conversations` agent envelope — the standard `/v1/chat/completions`
endpoint returns a hard 429 (`code 1300`) for every request. A local relay
converts between OpenAI chat/completions and Mistral conversations shapes.

The real key lives only in `~/.omp/guardian/secrets.json[mistral_glm_key]`
(read by the relay at process start) and in this conversation's NewAPI
channel row is a placeholder (`local-relay-no-auth`). No key is stored in
this repository or this document.

## Architecture

```
client → NewAPI ch85 (type=1, base_url=http://127.0.0.1:16001)
       → mistral-conversations-relay (loopback, supervisor-watched)
       → https://api.mistral.ai/v1/conversations (model glm-5-2)
```

- Relay source: `scripts/ops/mistral-conversations-relay.py`
- Deployed copy: `~/.omp/guardian/mistral-relay-16001/` (supervisor entry
  `mistral-relay-16001`, log `~/.omp/guardian/mistral-relay.log`)
- Channel helper: `scripts/ops/add_mistral_glm_channel.py` — idempotent;
  re-run performs dup-check + readback verification only
- Port contracts: `newapi-local-smoke.py` `PROXY_PORTS["mistral-relay"]`,
  `system-health-check.py` proxies dict (`mistral-relay 16001`)

## Conversion contract

Request (OpenAI → conversations):

| OpenAI field | conversations field |
|---|---|
| `messages` role=system | `instructions` (joined `\n\n`) |
| `messages` user/assistant | `inputs[]` |
| `max_tokens`/`temperature`/`top_p`/`stop`/`seed` | `completion_args` |
| model `zai-glm-5-2` | upstream `glm-5-2` (alias map) |
| `tools`/`tool_choice` | rejected 400 (fail-loud, no silent capability loss) |
| non-text content parts | rejected 400 |

Response: `outputs[]` entries of type `message.output` → `choices[0].message.content`;
`usage` passes through. Upstream HTTP errors keep their status code with an
OpenAI error envelope; network failures → 502.

Streaming: `stream:true` opens an upstream SSE stream and relays deltas live:

| conversations SSE event | OpenAI chunk |
|---|---|
| `conversation.response.started` | role chunk |
| `message.output.delta {content}` | content chunk |
| `conversation.response.done {usage}` | finish chunk + usage, then `[DONE]` |

If the upstream stream cannot be opened, the relay falls back to a buffered
non-stream call + synthesized SSE (never worse than the original contract).
Mid-stream upstream death appends a deterministic `[DONE]`; live SSE
responses close the connection after `[DONE]` (no Content-Length).

Measured 2026-08-16: TTFT 1.2s direct to relay, 4.7s end-to-end through
NewAPI (vs. full upstream latency with buffered synthesis).

## Pitfalls (all hit during bring-up)

1. **base_url must not contain `/v1`** — NewAPI appends `/v1/chat/completions`
   itself; storing `…/v1` produced `/v1/v1/chat/completions` 404.
2. **Keep-alive body drain** — `BaseHTTPRequestHandler` must consume
   `Content-Length` on every path that answers without reading the body
   (GET probes, 404s). A leftover body is parsed as the next request line
   and surfaces as `501 Unsupported method ('{"model":…}')`.
3. Windows port binding: relay uses `allow_reuse_address=False` on win32 so a
   duplicate process cannot shadow-bind the listening port (codex-relay
   convention).

## Verification evidence (2026-08-16)

- Channel + abilities readback after create: ch85 type=1 status=1,
  `zai-glm-5-2` enabled
- NewAPI admin channel test: `success=true`, 1.9s round trip
- End-to-end stream via `127.0.0.1:3002/v1/chat/completions`: 200 SSE,
  7 chunks, first content 4.7s
- Consume log: rows land with `channel_id=85`, model `zai-glm-5-2`
- Supervisor self-heal: relay killed twice, restarted and healthy both times

## Rollback

```powershell
# disable the channel ( Guardian/SQLite SSOT remains consistent )
POST /api/channel/85/status {"status": 2}
# remove supervisor entry: revert proxies-supervisor.py (repo + deployed
# copy), then restart the supervisor; kill the relay process
```

DB snapshot before channel creation:
`~/.new-api-local/backups/new-api-before-mistral-zai-glm-5-2-20260816-115441.db`.

## Related

- `scripts/ops/README.md` § 本地格式转换 relay
- Supervisor autostart: Startup `LocalAIProxies-Supervisor.lnk` is the sole
  live entry; the same-named scheduled task is a Disabled legacy (verified
  2026-08-16: briefly re-enabled during bring-up, then re-disabled to match
  the documented contract; single-instance mutex makes any duplicate exit)
