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

## Runtime verification

Post-change checks confirmed:

- Both option updates returned success from the management API.
- A read-back of all options showed the new values persisted.
- NewAPI system logs recorded both option changes.
- Normal GPT and Claude traffic continued after the changes.
- Failover remained active: recent Claude primary failures were followed by successful requests.

## Remaining risks

1. Grok routing is fragile. Channel 17 is slow and intermittent, channel 13 is rate-limited, and channel 19 is disabled pending a valid key.
2. Claude channels 9 and 18 show occasional upstream 500/502 failures. Current retry and failover behavior masks most failures, but upstream quality should be monitored.
3. Performance metrics are enabled while `perf_metrics_setting.retention_days = 0`; this prevents useful multi-day P95 latency analysis. A seven-day retention window is a reasonable next measurement step.
4. Prompt-cache ratio tables mainly contain older Claude model names. This is harmless for unlimited private use but makes cost and cache accounting incomplete for newer models.
5. The deployment remains a single SQLite-backed instance. Official NewAPI guidance recommends PostgreSQL and Redis for clustered/high-availability deployments; no migration is justified for the current single-node workload without capacity evidence.

## Rollback

Local pre-change artifacts:

- Full options snapshot: `tmp/newapi-options-backup-20260729-002426.json` (not committed)
- Channel 19 response backup: `tmp/ch19-backup-20260728-235003.json` (not committed; treat as sensitive operational data)

To roll back the global changes, restore these values through the option API:

```text
monitor_setting.auto_test_channel_enabled = false
AutomaticRetryStatusCodes = 100-199,300-399,401-407,409-499,500-503,505-523,525-599
```

To re-enable channel 19, first install its correct upstream key, verify a successful channel test, and only then set its status to enabled.

## References

- NewAPI environment variables: <https://www.newapi.ai/zh/docs/installation/config-maintenance/environment-variables>
- NewAPI repository: <https://github.com/QuantumNous/new-api>
