# NewAPI audit and tuning record (2026-07-29)

## Scope

This record covers a read-only channel and option audit followed by two approved global setting changes and one broken-channel correction. Secrets, API keys, access tokens, and VPS passwords are intentionally omitted.

NewAPI instance:

- Public endpoint: `https://aliyun.donglicao.com`
- Reported version: `v1.0.0-rc.21`
- Deployment: single NewAPI container with SQLite storage
- Active client path: clients connect directly to NewAPI; the former local cc-switch proxy path is retired

## Channel audit

Eleven channels existed at audit time. Ten remained enabled after remediation; channel 19 was disabled.

Later the same day a twelfth channel was added (`fengwind-grok`, id 20). See "Later changes" below.

Fresh channel tests showed:

| Channel | Role | Result |
|---:|---|---|
| 9 | Claude primary | Healthy, about 2 seconds in the audit sample; recent logs also contained intermittent 500/502 upstream failures |
| 18 | Claude backup | Healthy, about 3 seconds; one recent 502 was present in logs |
| 2 | GPT primary | Healthy, about 2 seconds |
| 16 | GPT backup | Healthy but slower, about 9 seconds |
| 3 | Claude alternate | Healthy, about 3 seconds |
| 12 | Multi-model alternate | Healthy but slower, about 9 seconds |
| 14 | GLM source | Healthy, about 3 seconds |
| 15 | SenseNova source | Healthy, about 2 seconds |
| 17 | Grok primary | Unstable and slow; repeated samples ranged from about 7 to 26 seconds with two failures in six tests |
| 13 | Grok backup | Persistent upstream group rate limit (`429`) |
| 19 | Grok alternate | Broken; see remediation below |

### Channel 19 root cause and remediation

Channel 19 (`gpt2api-grok`) was originally created by copying channel 17's SQLite row after the management API creation path failed. This introduced two independent defects:

1. Its base URL ended in `/v1`. NewAPI appends `/v1/chat/completions` for OpenAI-compatible channels, producing an invalid double-`/v1` path and a `404` response.
2. It inherited channel 17's upstream key. After the URL was corrected, the upstream returned `401 INVALID_API_KEY`.

Actions taken:

- Changed the base URL from `https://gpt2api.dpdns.org/v1` to `https://gpt2api.dpdns.org`.
- Confirmed the routing error changed from `404` to the underlying key error (`401`).
- Disabled channel 19 through the dedicated channel status endpoint.

Channel 19 must remain disabled until a valid key for its own upstream is installed and a channel test returns HTTP 200.

## Global option changes

The complete pre-change option response was backed up locally before mutation.

### Enable scheduled channel tests

Changed:

```text
monitor_setting.auto_test_channel_enabled: false -> true
```

Related settings left unchanged:

```text
monitor_setting.auto_test_channel_minutes = 10
monitor_setting.channel_test_mode = scheduled_all
ChannelDisableThreshold = 50
```

Reason: the legacy deployment note claimed automatic channel testing was active, but the runtime option was disabled. Broken channels therefore remained enabled until manually diagnosed.

### Stop retrying authentication failures

Changed:

```text
AutomaticRetryStatusCodes:
  before: 100-199,300-399,401-407,409-499,500-503,505-523,525-599
  after:  100-199,300-399,409-499,500-503,505-523,525-599
```

`RetryTimes` remains `4`.

Reason: retrying `401-407` authentication and permission failures cannot repair an invalid upstream key and multiplies latency and upstream load. Rate limits (`429`) and server failures remain retryable.

This value was superseded later the same day; see "Later changes".

## Runtime verification

Post-change checks confirmed:

- Both option updates returned success from the management API.
- A read-back of all options showed the new values persisted.
- NewAPI system logs recorded both option changes.
- Normal GPT and Claude traffic continued after the changes.
- Failover remained active: recent Claude primary failures were followed by successful requests.

## Remaining risks

1. Grok routing remains fragile. Channel 20 (`fengwind-grok`) was initially raised to priority 70 after it passed testing, but later produced repeated 120-second `524` timeouts and is now manually disabled. A fresh three-run `grok-4.5` test returned `502` each time; `grok-3-mini`, `grok-4.3`, and `grok-4.20-0309-non-reasoning` also returned `502`. Only `grok-composer-2.5-fast` still passed, slowly (~57 seconds), which is not sufficient reason to re-enable the whole channel. Current successful `grok-4.5` traffic uses channel 17; channel 13 remains another imperfect spare, and channel 19 stays disabled pending a valid key.
2. Claude channels 9 and 18 show occasional upstream 500/502 failures. Current retry and failover behavior masks most failures, but upstream quality should be monitored.
3. ~~Performance metrics retention was 0~~ **Done (2026-07-29):** `perf_metrics_setting.retention_days` set to `7` (metrics already `enabled=true`, `bucket_time=hour`). Multi-day P95 can accumulate from this point; older samples before the change remain unavailable.
4. ~~Prompt-cache ratio tables mainly contain older Claude model names~~ **Done (2026-07-29):** two fill rounds. Round 1: `CacheRatio` +26 / `CreateCacheRatio` +14 for kimi-active models. Round 2: `CacheRatio` +5 / `CreateCacheRatio` +14 for remaining enabled-channel models (gemini/nemotron/grok-build and create-side secondaries). Final sizes **104 / 70**. Enabled channel models except router token `auto` now have **zero miss** on both tables. Ratios: cache read 0.1 (deepseek-class 0.25); create 1.25. Snapshots: `tmp/naopts-cache-disk-before.json`, `tmp/naopts-cacheratio-round2-before.json` (local only).
5. The deployment remains a single SQLite-backed instance. Official NewAPI guidance recommends PostgreSQL and Redis for clustered/high-availability deployments; no migration is justified for the current single-node workload without capacity evidence.
6. ~~`performance_setting.disk_cache_enabled` was false~~ **Done (2026-07-29):** set to `true`; `disk_cache_max_size_mb=1024`, `disk_cache_threshold_mb=10`, path left empty (NewAPI default under its data dir). Revisit if VPS disk pressure appears.

## Later changes (same day, after this record was first committed)

The following changes were made after the sections above were written. They supersede the earlier values where they overlap.

### Retry status codes widened to cover gateway timeouts

```text
AutomaticRetryStatusCodes:
  before: 100-199,300-399,409-499,500-503,505-523,525-599
  after:  100-199,300-399,409-499,500-504,505-599
```

Reason: the earlier range skipped `504` (gateway timeout) and `524`. For models served by more than one channel, a timeout now triggers failover to another upstream instead of surfacing as a client-side timeout. `RetryTimes` remains `4`. `401-407` stays non-retryable.

### SSE keepalive enabled

```text
general_setting.ping_interval_enabled: false -> true
general_setting.ping_interval_seconds: 60 (unchanged)
```

Reason: high-effort reasoning requests can stay silent for tens of seconds, which intermediate proxies may treat as a dead connection.

Correction: an earlier attempt wrote the flat key `ping_interval_enabled`, which NewAPI does not read. That write left `general_setting.ping_interval_enabled` at `false`, so keepalive was not actually active until the namespaced key was set. Verified by read-back.

### Grok upstream added (channel 20)

A new OpenAI-compatible channel `fengwind-grok` was added, serving Grok models including `grok-4.5`, `grok-4.3`, the `grok-4.20-*` snapshots, and `grok-composer-2.5-fast`. Initial `priority` was 40 (below 17 and 13), then corrected to 70 after confirming that NewAPI routes higher priority numbers first. This paragraph records the initial successful state; it is superseded by the current outage note below.

Current state: channel 20 is manually disabled after repeated `524` timeouts. Fresh tests returned `502` for `grok-4.5` three times and also failed for `grok-3-mini`, `grok-4.3`, and `grok-4.20-0309-non-reasoning`. `grok-composer-2.5-fast` still passed but took about 57 seconds. Do not re-enable the whole channel based on that single slow model; current successful `grok-4.5` traffic uses channel 17.

Two findings worth recording:

- Creating a channel through `POST /api/channel/` requires the payload shape `{"mode":"single","channel":{...}}`. A flat channel object leaves the `Channel` pointer nil and the handler panics with a nil-pointer dereference (`controller/channel.go`, `AddChannelRequest.Channel` is `*model.Channel`). This is a client-side payload error, not a server defect, but the error surface is a 500 panic rather than a validation message.
- `grok-composer-2.5-fast` had no entry in `ModelRatio` / `CompletionRatio` and returned a price-not-configured `400`. Input ratio `0.5` and completion ratio `2` were added to match the other Grok models. All other Grok models already had ratios.

### Channel 15: remove GLM after sustained TPM throttling

Channel 15 (`sensenova-token`) no longer serves Grok. It was temporarily used for `glm-5.2`, `deepseek-v4-flash`, and `sensenova-6.7-flash-lite`, but production GLM traffic exposed a limit that small channel tests did not: 21 of 22 recent channel-15 GLM records failed with upstream `429` because requests exceeded the shared 5,000,000 TPM limit. The affected Kimi session was sending roughly 420k-430k prompt tokens per request, with no upstream cache tokens reported. NewAPI retries could hit channel 15 repeatedly before falling back to channel 14, multiplying token pressure and latency.

Action: removed `glm-5.2` from channel 15 `models`, disabled only its channel-15 ability, and restarted NewAPI. Channel 15 remains enabled for `deepseek-v4-flash` and `sensenova-6.7-flash-lite`; both passed post-change channel tests. A real Kimi GLM request then succeeded directly through channel 14, with no channel 15 hop or new channel 15 error. Server backup: `one-api.before-ch15-remove-glm-20260729-130018.db`.

### Additional GPT sources and protocol isolation

Channel 21 (`191.96.25.96-gpt-backup-http`) was added as a low-weight OpenAI-compatible GPT backup (`priority=50`, `weight=3`). Direct chat, streaming, and tool-call tests passed. It remains enabled, but its public upstream transport is plain HTTP; use it only as a backup because API credentials and request content are not protected by TLS between NewAPI and the upstream.

Channels 22 and 23 were created for AgentRouter Claude/OpenAI traffic but remain disabled. The alternate HTTPS domain works from the local workstation, while the Aliyun VPS receives an Aliyun WAF browser challenge page; the original domain is unreachable from that VPS. Their abilities are disabled so production routing cannot select them. Kimi Code can still use AgentRouter directly from the workstation through separate providers; no API keys are recorded here.

Tailscale is installed, so a workstation relay is technically possible: NewAPI could target a service exposed on the workstation's Tailscale address, and that service could forward to AgentRouter using the required client headers. This has not been implemented or validated. There is currently no relay process, listening port, service supervision, health check, or sleep/offline policy; therefore Tailscale installation alone does not make channels 22/23 usable. A workstation relay would also make production availability depend on the Windows machine remaining online.

Channel 24 (`welfare-0xpsyche-responses`) is an enabled Responses-only source. The upstream accepts only `/v1/responses` with a Codex-style user agent. To prevent ordinary Chat Completions traffic from selecting it, NewAPI exposes the isolated model alias `welfare-codex-gpt-5.6-sol`, mapped upstream to `gpt-5.6-sol`. It runs at `priority=40`, `weight=2`, with matching price ratios (`ModelRatio=0.5`, `CompletionRatio=2`, `CacheRatio=0.1`). Automatic and streaming channel tests passed, and a Kimi CLI request through the shared gateway logged a successful channel 24 hit with no type-5 errors.

Kimi Code uses an `openai_responses` provider for `zg-newapi/welfare-codex-gpt-5.6-sol`. The ordinary `zg-newapi/gpt-5.6-sol` alias remains on Chat Completions and is intentionally not routed to channel 24.

Channel affinity initially did not apply to this isolated alias: the enabled `codex cli trace` rule matched only `^gpt-.*$`, so `welfare-codex-gpt-5.6-sol` failed the model-name test even though `/v1/responses` matched. After backing up the database to `/opt/new-api/backups/one-api.before-welfare-affinity-20260729-140620.db`, the rule was widened to `^(?:gpt-.*|welfare-codex-gpt-.*)$`. Its key source (`prompt_cache_key`), path filter, and 300-second TTL were left unchanged.

Post-change verification showed that the Welfare alias and Responses path both match, the seven-rule set otherwise remained identical, both database integrity checks returned `ok`, `/api/status` returned HTTP 200, and the restarted container reported no critical startup errors. To roll back only this change, restore the Codex rule's model regex to `^gpt-.*$` and restart NewAPI.

### Channel 12: remove gpt-5.6-sol (stop fixed 503 path)

Logs showed most `gpt-5.6-sol` type-5 errors as `503` on channel 12 (`vyceai`), then failover to channels 2/16 (`use_channel` multi-hop). Channel 12 models included a single GPT entry `gpt-5.6-sol` that the upstream consistently marked unavailable.

Action: PUT channel 12 `models` without any `gpt-5*` id. Remaining models unchanged (claude/glm/deepseek/minimax/mimo/gemini/nemotron/auto). Backup: `tmp/ch12-before-remove-gpt.json` (local only).

Post-check:

- Channel 12 models no longer list `gpt-5.6-sol`.
- Channel 2 and 16 still list the full gpt-5.5 / 5.6 family.
- Channel 12 tests: `claude-haiku-4-5` / `deepseek-v4-flash` / `minimax-m3` success; `claude-fable-5` still upstream 503 (pre-existing, not caused by this edit).

## Rollback

Local pre-change artifacts:

- Full options snapshot: `tmp/newapi-options-backup-20260729-002426.json` (not committed)
- Channel 19 response backup: `tmp/ch19-backup-20260728-235003.json` (not committed; treat as sensitive operational data)

To roll back the global changes, restore these values through the option API:

```text
monitor_setting.auto_test_channel_enabled = false
AutomaticRetryStatusCodes = 100-199,300-399,401-407,409-499,500-503,505-523,525-599
general_setting.ping_interval_enabled = false
```

That `AutomaticRetryStatusCodes` value restores the state before *any* of the day's changes. To undo only the later widening and keep the authentication fix, use `100-199,300-399,409-499,500-503,505-523,525-599` instead.

To remove the Grok upstream, disable channel 20 through the channel status endpoint. Its `ModelRatio` / `CompletionRatio` entry for `grok-composer-2.5-fast` can stay; an unused pricing entry is harmless.

To re-enable channel 19, first install its correct upstream key, verify a successful channel test, and only then set its status to enabled.

## References

- NewAPI environment variables: <https://www.newapi.ai/zh/docs/installation/config-maintenance/environment-variables>
- NewAPI repository: <https://github.com/QuantumNous/new-api>
