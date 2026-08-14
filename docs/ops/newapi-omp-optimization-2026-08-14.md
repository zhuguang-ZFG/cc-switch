# NewAPI / OMP routing hardening (2026-08-14)

## Final Opus capability decision

The temporary 110k route workaround described below was superseded later on
2026-08-14. `zg-newapi-anthropic/claude-opus-5.contextWindow` is restored to
the official model capability of `200000`, and the route gate now requires that
exact value. The 2026-08-09 Kiro/NewAPI aggregate failure around 130k-140k
tokens remains valid operational evidence, but it is a route-specific risk and
must not be encoded by understating the model capability.

No active OMP process was restarted. New processes read 200k; an existing
process adopts it through its normal reload/restart lifecycle.

Final verification after restoration:

- update-helper and OMP-route suites: 36 tests passed;
- smoke and system-health unit suites: 28 tests passed;
- live `system-health-check.py --json`: 24/24 passed;
- default updater dry-run: `current=200000 proposed=200000`, already configured.

## Baseline findings

- NewAPI affinity was enabled, but six rules did not match their `zg-*` aliases.
- Channels 9 and 18 had re-entered the Claude Opus 5 pool despite the intentional
  quarantine contract. Three direct channel tests per channel failed; a follow-up
  response inspection confirmed HTTP 503 `No available accounts` for both.
- `zg-newapi-anthropic/claude-opus-5` had been reduced from its official 200k
  capability to a 110k workaround after the aggregate route hard-400ed some
  requests around 130k-140k. The final decision above separates those concerns.
- Codex relay ports 15999 and 16000 each had exactly one process and one matching
  listener. Repository and both runtime relay copies had identical SHA-256, so no
  relay restart or process termination was performed.
- `LocalNewAPI-Watchdog` initially reported 51 missed runs and a stale last-run
  timestamp. Its next scheduled invocation completed with result 0; no watchdog
  file or task mutation was required.

## Changes

1. Updated all seven affinity rules to cover canonical and `zg-*` model ids.
   Existing path, TTL, key-source, and other rule fields were preserved.
2. Double-locked channels 9 and 18 with `status=2` and `weight=0` after repeated
   direct failure evidence.
3. Initially changed only `zg-newapi-anthropic/claude-opus-5.contextWindow`
   from 200000 to 110000 as a route workaround. This was later reversed by the
   final official-capability decision above.
4. Added repository gates:
   - affinity alias validation;
   - critical DeepSeek ability priority/weight validation after channel PUT;
   - exact relay process/listener ownership validation;
   - Windows UTF-8 decoding for `omp models` route tests;
   - removal of stale CodeBuddy provider expectations after the 8787 retirement.

## Backups

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| `~/.new-api-local/backups/channel-affinity-20260814-135725.json` | 7052 B | `310C3B77E2E21CC5302ED20F72BDDCAD56C16C2D14298FCB943DF3DD5C43B278` |
| `~/.new-api-local/backups/channels-9-18-20260814-135822.json` | 3397 B | `8C103C4EF6EB86CE5AA4DC440B81CB892935C8725F681A89F58BFE9F18B1DB36` |
| `~/.omp/agent/models.yml.20260814-135915-opus5-context.bak` | 4540 B | `C32B683D3A940CBB9511994AFA2A7317E6527A9651A3448FC5214D9C9B20A36E` |
| `~/.omp/agent/models.yml.20260814-141722-opus5-context.bak` | 4540 B | `2FE49E4F0954A99F332B4AF7B19798AC48B875988D108EEF7B166A2D65EE3D14` |

The channel backup contains live channel credentials and must not be shared or
committed. Backup structure was checked without printing secret values: two
channel records with ids 9 and 18 were present.

## Verification

- Update/smoke/health/route regression group: 63 tests passed.
- Full relevant suites run independently (Guardian, smoke, OMP route, relay,
  supervisor, health, update helpers): 228 tests passed.
- `newapi-local-smoke.py`: all checks passed, including affinity, quarantine,
  critical abilities, multi-key health, and two non-stream Chat Completions:
  `sensenova-6.7-flash-lite` and `opencode-go`, both HTTP 200.
- `system-health-check.py --json`: 24/24 passed. NewAPI 3002, semantic gateway
  3003, CC Switch 15721, both relay ports, Guardian, supervisor, watchdog, and
  backups were healthy.
- The final OMP route gate requires an exact 200k context window. This is a
  config/CLI resolution check, not a live Claude inference or forced fallback test.
- All three update scripts were rerun in dry-run mode and reported no remaining
  drift or additional changes.

## Rollback

- Affinity: PUT the two saved option values from the affinity backup back through
  the single-option `/api/option/` contract, then rerun the smoke check.
- Channels 9/18: restore each saved channel object with PUT, then enable through
  the status endpoint only after a bounded 2-of-3 recovery probe succeeds.
- OMP context: the 14:17 backup restores the superseded 110k workaround; use it
  only for an explicit route-risk rollback. The official-capability target is
  200k. Reload/restart OMP through its normal lifecycle, and do not replace an
  active session process solely for rollback.
