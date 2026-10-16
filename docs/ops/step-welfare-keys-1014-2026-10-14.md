# Step (阶跃星辰) welfare keys 2026-10-14

## Keys

Both were posted publicly (forum welfare threads) and burn fast. Stored in
`~/.new-api-local/step-plan-keys-1014.txt` (two lines, never printed; len=65 each,
sha12 `247128d4881d` line 1, `3d1f6e36e676` line 2).

| key | endpoint that accepts it | first probe | ~15–30 min later |
|---|---|---|---|
| line 1 | `https://api.stepfun.ai/step_plan` (ch205 contract) | 200 ×4 chat models | step-3.7-flash 200; step-5-preview / step-3.5-flash / step-3.5-flash-2603 **429 `concurrency reached, current: 9, limit: 8`** |
| line 2 | `https://api.stepfun.com/v1` (ch216 contract) | 200 ×2 models | **402 `quota_exceeded` on every model** |

Public keys are strip-mined within minutes: line 2 was drained between two probes, and line 1
stays saturated by third-party concurrency. Line 2's `402` was caught by the rotation script's
direct gate before any DB/API write.

Key-shape + endpoint exclusivity (2026-10-14, deterministic):
- Both keys are **raw keys, not base58 blobs**: key1 contains `0`,`I`; key2 contains `I`,`O`,`l`
  (chars outside the base58 alphabet). Probing did not need decoding — the 200s confirm the raw
  string is the credential.
- Key1 is **step_plan-only**: at `api.stepfun.com` it returns 401 `invalid_api_key`.
  Key2 was **official-only**: at the step_plan endpoint it returned 401.
- The original archived blob (`step-plan-key-drained-20261014.txt`, base58) decodes to two keys,
  both exhausted: `you have no left credit for step plan` (429). No third leg exists.

## ch205 → ch224 (plan key — done)

- `scripts/ops/add_step_plan_channel.py --teardown --apply` deleted ch205 (snapshot
  `new-api-before-step-plan-20261014-163045.db`, integrity ok), then bounded `--apply` retries
  (≤4, 20 s apart).
- Attempts 1–2 were refused at the upstream gate (`step-3.5-flash` 429 →
  `refusing to register a partly-dead pool`). The gate was **not** weakened: every attempt still
  required all four chat models to answer in one snapshot. Attempt 3 passed.
- Created **ch224** `step-plan` (p0/w5, `auto_ban=1`, six ids, same base_url/group as ch205).
  The key file now holds the base58 blob of line 1; the drained original blob is kept as
  `step-plan-key-drained-20261014.txt`.
- Verification: management probes 4/6 (stepaudio ×2 = audio-only 404 by design; step-3.5-flash
  one transient upstream error), then gateway relays step-3.7-flash 200 (4.57 s / stream 15 chunks)
  and step-5-preview 200 (1.62 s / 21 chunks) with **6 log rows attributed to ch224, zero-output
  none**. Snapshot `new-api-before-step-plan-20261014-163348.db`.
- ch224 is now the only enabled step leg (ch184 / ch216 stay disabled).

## ch216 (official key — not applied)

- New script `scripts/ops/rotate_stepfun_official_key.py` (dry-run verified) rotates ch216's key
  via PUT and restores the documented **p0/w1** posture — the 2026-10-12 p10 bump was an incident
  posture whose premise (dead ch205) no longer holds. It then runs fix: enable (status POST 1)
  runs **before** the abilities assertion, because a disabled channel keeps `abilities.enabled=0`
  and would otherwise fail the `wait_abilities` check right after a successful PUT — the key is
  already rotated at that point, so enable-first carries no dead-key auto-ban risk. The
  attribution check accepts `{224, 216}` since both now share the p0 tier.
- It stopped at the direct gate: key2 answers `402 quota_exceeded` on all models. ch216 keeps its
  old key and stays disabled. The script is ready to run if the plan refills.

## Watch items

- ch224's upstream key is saturated by third parties; per-request 429s are expected. 429 is not in
  this fork's auto_ban table, so the leg degrades intermittently instead of dropping out — but its
  `auto_ban=1` still reacts to entitlement errors.
- Welfare keys can 402 without notice (as key2 did). Re-probe before relying on either.

---

## 快照引用状态（2026-10-16 补记）

本页引用的整库快照已被轮转回收（`backups/` 为 KEEP=7 轮转缓存，非归档；
见 `backup-rotation-lexicographic-bug-2026-10-16.md`）：

- `new-api-before-step-plan-20261014-163045.db` —— 本 bug（supervisor 轮转按字典序删除）+ KEEP=7 设计
- `new-api-before-step-plan-20261014-163348.db` —— 本 bug（supervisor 轮转按字典序删除）+ KEEP=7 设计

回滚请以本页的逐字段步骤为准（快照只是加速器，不是前提）。
