# ch127 Multi-Key Flip Runbook — 2026-09-13

## Outcome

ch127 (`agentrouter-codex-gpt`) converted from single-key to 2-key polling:
`channels.key` = keys[0]`\n`keys[1] (keys.json pool), `channel_info` BLOB with
`is_multi_key/multi_key_size=2/multi_key_mode=polling`. Polling rotation is
mechanically verified (each request consumes one polling slot). Extension to 4
keys is gated on a cross-key reasoning-replay identity test (§9) that must pass
first; the test is automated and blocked only on upstream GPT budget-pool
recovery (refill 00:00/08:00/16:00 Asia/Shanghai).

## Procedure (flip without losing channel id)

Deleting/recreating the channel would move channel_id and break session
affinity (affinity pins channel_id; §9 brick path). Instead:

1. **Backup** channels 126/127/128 + abilities rows to a rollback JSON with
   keys redacted to fingerprints.
2. **DB direct write**: `UPDATE channels SET key=?, channel_info=? WHERE id=127`
   with multi-line key AND `sqlite3.Binary(json.encode())` — see TEXT trap
   below. Keep `multi_key_polling_index` as-is.
3. **Harmless PUT** via admin API (GET → drop `status` key → set real key
   explicitly — GET responses mask the key to empty string) to trigger
   `InitChannelCache()`. DB write alone leaves routing on the stale cached
   state (documented in local-gateway-hardening-2026-08-05.md, ch9 precedent).
4. **Readback** from both DB and admin API; verify abilities untouched.

## TEXT storage trap (caused a 9-minute degraded window)

The fork's `ChannelInfo.Scan` asserts `value.([]byte)`. A channel_info row
stored as TEXT (what a plain python `str` bind produces) fails the assertion →
nil → `failed to get channels: sql: Scan error on column index 27` on every
`GetAllChannels` (cc-switch admin polls this every ~17s). Effect: the whole
channel list read fails, excluding ch127 from routing until fixed.

- Every python DB write of channel_info MUST use `sqlite3.Binary(...)` (BLOB),
  matching the Go-written rows.
- Detection: `SELECT typeof(channel_info), count(*) FROM channels GROUP BY 1`
  — expect exactly one storage type.
- Recovery: rewrite the TEXT row as BLOB, then the harmless PUT (step 3).
- This failure mode is now a `verify_projection` regression check
  (test_projection_multi_key_health asserts BLOB storage).

## Budget pool is account-level, not per-key

Direct evidence 2026-09-13: with ch127 in 2-key polling, consecutive
`test/127?model=gpt-5.6-sol` calls 402'd on BOTH keys (keys[0] heavily used,
keys[1] never consumed) while polling advanced 0→1→0. Consistent with 09-10
(§8.2: all 4 pool keys 402'd simultaneously). Conclusion: the 4-key pool adds
per-key rate-limit spread, NOT capacity; capacity is the shared budget pool.
Recovery is refill-schedule-driven (00:00/08:00/16:00), sometimes earlier.

## Single-key era brick precedent

17:05-17:28 on 2026-09-13, ch127 (single key) already produced
`The encrypted content for item rs_... could not be verified` for 3 distinct
codex sessions. Reasoning-replay identity can drift inside agentrouter
independently of which NewAPI key is used; multi-key rotation is not
necessarily the brick source. The §9 two-turn test (turn1 on one polling key,
resume turn2 rotating to the other) is the decisive experiment for whether
multi-key makes it worse.

## Script contract changes

`configure_codex_agent_any.py`:
- `agent_payload(keys: list[str])` — joins the full pool key list with `\n`.
- Cold start POSTs `{"mode": "multi"}` when the pool has >1 key (fork computes
  channel_info from the key lines) — POST mode:"single" cannot enable
  multi-key. Not exercised in production yet (cold start only; runbook note).
- Existing channels are verify-only (never PUT) — re-apply cannot corrupt a
  multi-key channel's metadata.
- `verify_projection` now also checks key shape health: multi-key metadata
  must match stored key line count; single-key channel must store one line;
  every line must pass `usable_key`; channel_info must be BLOB-typed.

## Rollback

`bak-ch127-multikey-pre-20260913.json` (3 channels + 5 abilities, keys as
fingerprints) + `tmp-rollback127.py`: restores single key = keys[0] with BLOB
channel_info, then the cache-refresh PUT, then readback asserts single-key.

## Open items

- §9 identity verification: automated in `tmp-watch127.py` (hub process
  `ch127-watch`), PASS keeps 2-key config, FAIL auto-rolls back.
- If verification passes, extend to 4 keys via the same flip procedure
  (BLOB write size=4 + harmless PUT) and re-verify abilities.

## 投放规则公告（2026-09-13，agentrouter 渠道公告）

1. **gpt-6-astra 已上线**——ch127 模型集（gpt-6-astra,gpt-5.6-sol）已覆盖，无需配置变更。
2. **资源投放改为北京时间 0:00 / 8:00 / 16:00 三个时段，用完即止**（9月10日起）。
   这解释了 09-13 16:00 投放后 watcher 17:55–21:18 持续 402：投放量约支撑 2 小时即耗尽。
   含义：池恢复探测必须把 402 当作常态背景，验证窗口要贴着投放点（0:00/8:00/16:00 后
   越早越好）；watcher 的 180s 轮询在窗口期够用，但 §9 两轮 codex 测试要抓紧时间——
   若 turn1 在池再次耗尽前跑不完会 ABORT（无配置变更，可安全重试）。

**2026-09-13 21:55 处置**：watcher 已于 21:49 重启（deadline 至 09-14 ~04:49，覆盖 0:00
投放窗口）；若 TIMEOUT，则在 8:00 窗口按同法重启。

## 4-key 全池扩展（2026-09-13 22:20，用户决策显式取代 §9 gate）

用户指示"agent 是四 key"：2-key 过渡态直接扩展为 keys.json 全池 4-key。
脚本 `tmp-extend127-4key.py`：备份（`bak-ch127-4key-pre-20260913.json`，
fingerprint 级）→ DB BLOB 写 4 行 key + channel_info size=4 → 全对象 PUT
刷新缓存 → readback 全过（4 行 key、typeof=blob、size=4、polling、
abilities 未动）。§9 验证改为 4-key 下进行（见下）。

## watcher 猝死根因与修复（22:22）

21:49 与 18:01 两次 watcher 启动后 1–4 轮即消失，根因相同：**pythonw 经
Git Bash 启动时继承 bash 的 stdout pipe，bash 命令结束/超时被回收后 pipe
断开，watcher 下一轮 `print()` 抛 OSError 死亡**（log() 先写文件再 print，
文件里最后一轮完整但进程已死）。修复：`tmp-watch127.py` 的 `log()` 对
stdout 异常容错（文件日志为准）；改用 `Start-Process -WindowStyle Hidden`
脱离 bash 启动。22:22 重启后两轮以上存活正常（PID 22684）。

同时按用户决策移除 FAIL 自动回滚（原逻辑 FAIL → tmp-rollback127.py 回滚
单 key）：4-key 是用户保底决策，FAIL 只记录 verdict 待人工分诊
（scrub / 新会话 / 减 key），不允许无人值守回滚。
