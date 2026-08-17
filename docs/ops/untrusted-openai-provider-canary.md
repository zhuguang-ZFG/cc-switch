# Untrusted OpenAI-compatible provider canary

Use `scripts/ops/probe_untrusted_openai_provider.py` to collect bounded,
redacted compatibility evidence from a provider whose catalog or model names
cannot be trusted. The initial target is `www.sotamodel.net`, whose historical
`max`/`xhigh` aliases returned the base model id and whose non-stream GPT path
returned an empty SSE response.

This tool does not identify the real upstream model. Behavioral output and
self-reported model ids cannot prove provenance. It detects observable
contract violations and limits the provider to manual-canary status.

## Safety contract

- Dry-run by default; network access requires `--run`.
- API key comes only from `SOTAMODEL_API_KEY` or the environment variable named
  by `--api-key-env`. There is no key CLI argument.
- Requests are serial, use synthetic prompts, and have bounded time and body
  size. Source code, repository content, memory, and user data are never sent.
- Reports contain model ids, status, wire format, timing, usage counters,
  boolean semantic/tool results, and issue categories. They never contain
  prompts, response text, headers, tokens, or authorization values.
- Catalog/response model ids are limited to a 128-character safe identifier
  alphabet; rejected ids are counted but never rendered.
- The tool does not create/enable a NewAPI channel or edit OMP. A separately
  managed `sotamodel-canary` OMP provider may expose the verified catalog for
  explicit `--model` calls only. Route tests forbid any `sotamodel*` selector
  in `modelRoles` or `fallbackChains`.

## Usage

Inspect the plan without a key or network request:

```powershell
python3 scripts/ops/probe_untrusted_openai_provider.py
```

Run the bounded suite after setting the key in the current process environment:

```powershell
$env:SOTAMODEL_API_KEY = '<temporary canary key>'
python3 scripts/ops/probe_untrusted_openai_provider.py --run
Remove-Item Env:SOTAMODEL_API_KEY
```

Use `--models` to restrict model ids, `--max-models` to cap catalog matches,
and `--skip-tool` / `--skip-cache` for a smaller probe. `--tool-model` and
`--cache-model` choose the single models used for those checks.

## OMP manual provider

The local OMP registration is named `sotamodel-canary`. It contains only the
seven ids returned by the authenticated catalog and is invoked explicitly,
for example:

```powershell
omp -p "Reply with exactly: CANARY_OK" --model sotamodel-canary/model-A --no-session --no-tools --no-skills --no-rules --no-extensions
```

The provider reads its credential from the user-level
`SOTAMODEL_API_KEY`; `models.yml` contains a command resolver, not the key.
Its local `contextWindow: 1000000` and `maxTokens: 128000` values are a
permissive test envelope so OMP does not prevent deliberate boundary probes.
They are not measured capabilities, pricing evidence, or proof of the claimed
model identity. All entries remain text-only with reasoning disabled unless a
separate protocol test proves those request shapes.

## Evidence meanings

| Finding | Interpretation |
| --- | --- |
| `response-model-mismatch` | Requested and returned model ids differ; this proves mapping, not the real backend identity |
| `alias_collapses` | Multiple advertised ids return the same response id; do not register them as distinct OMP capabilities |
| `wire_model_variants` | One requested id returns different model ids for JSON and SSE; model identity is protocol-dependent and untrustworthy |
| `stream-false-returned-sse` | Provider ignored the requested non-stream wire contract |
| `empty-semantic-output` | HTTP success did not produce text or a tool call |
| `usage-missing` | Cost/cache/context accounting cannot be trusted for that shape |
| `suspicious_first_request_cache_hit` | A unique first request reported cache reads; treat cache accounting as provider-defined |
| `required-tool-call-missing` / `tool-arguments-invalid` | Provider is unsuitable for OMP tool workflows |

Regardless of probe results, the report sets `production_eligible=false`.
Promotion to a real route would require a separate decision, a new isolated
channel id, verified rollback, repeated protocol tests, and explicit proof
that the source adds an independent failure domain. A second key on the same
host normally adds quota diversity only, not provider diversity.

## Sotamodel evidence (2026-08-17)

The bounded live canary used only synthetic exact-text and tool prompts. The
current authenticated catalog returned seven ids:
`claude-opus-5`, `claude-opus-5-max`, `claude-opus-5-xhigh`, and opaque
`model-A/T/O/S`. No GPT model was present.

- Non-stream requests for all three Claude ids returned
  `response.model=claude-opus-5`.
- Streaming `claude-opus-5` returned `claude-opus-5-max`; streaming max and
  xhigh returned their requested aliases.
- Every opaque id returned `claude-opus-5` in JSON mode and
  `claude-opus-5-max` in SSE mode. All four therefore collapsed to one
  protocol-dependent identity.
- Exact semantic output and usage were present for all tested shapes. The
  required tool call was valid, but again changed the base request to the max
  response id.
- The two-request unique cache check reported zero cache-read tokens on both
  requests. The older suspicious first-request cache evidence did not
  reproduce in this run.

These results prove inconsistent naming and alias routing; they do not prove
that the actual upstream is Anthropic Claude. The source remains useful only
as a non-sensitive manual conformance canary.
