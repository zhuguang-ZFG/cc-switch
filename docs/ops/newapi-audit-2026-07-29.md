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

1. Grok routing depends on a single working upstream. Channel 20 (`fengwind-grok`) is the only Grok channel that passes a test. After a review finding that NewAPI uses **higher priority number first** (`ORDER BY priority desc`), channel 20 was raised from priority 40 to **70** (above 17/60 and 13/50) so requests hit fengwind first instead of burning two `429`s on 17/13. Channels 13 and 17 remain enabled as recovery spares; channel 19 stays disabled pending a valid key.
2. Claude channels 9 and 18 show occasional upstream 500/502 failures. Current retry and failover behavior masks most failures, but upstream quality should be monitored.
3. Performance metrics are enabled while `perf_metrics_setting.retention_days = 0`; this prevents useful multi-day P95 latency analysis. A seven-day retention window is a reasonable next measurement step.
4. Prompt-cache ratio tables mainly contain older Claude model names. This is harmless for unlimited private use but makes cost and cache accounting incomplete for newer models.
5. The deployment remains a single SQLite-backed instance. Official NewAPI guidance recommends PostgreSQL and Redis for clustered/high-availability deployments; no migration is justified for the current single-node workload without capacity evidence.

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

A new OpenAI-compatible channel `fengwind-grok` was added, serving 15 Grok models including `grok-4.5`, `grok-4.3`, the `grok-4.20-*` snapshots, `grok-composer-2.5-fast`, and the `grok-imagine` image/video models. Initial `priority` was 40 (below 17 and 13). **Corrected to priority 70** after confirming NewAPI FAQ/source: higher number = higher priority. Channel test after the change still returns success (~2.6s).

Two findings worth recording:

- Creating a channel through `POST /api/channel/` requires the payload shape `{"mode":"single","channel":{...}}`. A flat channel object leaves the `Channel` pointer nil and the handler panics with a nil-pointer dereference (`controller/channel.go`, `AddChannelRequest.Channel` is `*model.Channel`). This is a client-side payload error, not a server defect, but the error surface is a 500 panic rather than a validation message.
- `grok-composer-2.5-fast` had no entry in `ModelRatio` / `CompletionRatio` and returned a price-not-configured `400`. Input ratio `0.5` and completion ratio `2` were added to match the other Grok models. All other Grok models already had ratios.

### Channel 15 repurposed

Channel 15 (`sensenova-token`) no longer serves Grok. It now serves `glm-5.2`, `deepseek-v4-flash`, and `sensenova-6.7-flash-lite`; all three pass a channel test. Any note that describes channel 15 as a Grok source is stale.

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
