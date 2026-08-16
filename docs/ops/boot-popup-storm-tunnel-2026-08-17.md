# Boot popup storm RCA + fixes (2026-08-17)

## Symptom

After the 00:03:26 boot, a storm of white console-window flashes. Evidence:
`~/popup-hunt.log` (popup_hunter.py process-table watcher) recorded **132
console-process spawns** in the 00:05–00:10 logon window.

## Root cause (primary, ~45+ flashes and counting until fixed)

`newapi-edge-tunnel.vbs` (startup folder → wscript) supervises the reverse
tunnel `ssh -N -R 127.0.0.1:3000:127.0.0.1:3002 lima` with a **fixed 5-second
no-backoff Do...Loop**.

The local reboot orphaned the pre-reboot session's server-side sshd process
(lima pid 3775644, started 00:00:09), which kept holding the remote forward
on 127.0.0.1:3000. Every new ssh attempt then died in ~0.5s with
`remote port forwarding failed for listen port 3000` (ExitOnForwardFailure)
→ vbs respawned every ~6s → one conhost flash per respawn, indefinitely.

## Fixes applied (2026-08-17 00:13–00:19)

1. **Killed the stale forward**: `ssh lima "kill 3775644"` → port 3000 freed
   → next vbs iteration established the tunnel (00:13:49) →
   `https://aliyun.donglicao.com/api/status` back to 200. Verified zero ssh
   popups in the following window.
2. **vbs hardened** (`~/.cursor-byok-router/newapi-edge-tunnel.vbs`):
   - ssh wrapped in `conhost.exe --headless` (machine's proven windowless
     pattern, same as LocalNewAPI-Watchdog)
   - exponential backoff on fast failures: 5s→15→45→135→300s cap; a run
     living ≥60s resets to 5s (healthy-tunnel quick restart)
   - Hot-swapped the running supervisor (killed old wscript+ssh, started new
     vbs): tunnel re-established 00:18:02, public endpoint 200, zero console
     spawns in a 15s observation window.
3. **AnyRouter Window Canary scheduled task**: action changed
   `python.exe` → `pythonw.exe` (was a periodic console flash).

## Remaining popup sources (deferred)

- **MCP server launch chains** at OMP session start: `mcp-server-filesystem.cmd`,
  `npx -y mcp-rubber-duck` (cmd→npx→node = 3 console windows per launch),
  claude-mem `worker-service.cjs` spawning powershell/cmd,
  `fz_mcp_server.py` via console python.exe. Fix = convert each MCP launcher
  in OMP/Claude config to a windowless wrapper; not done (scope).
- OMP's own `git status` calls dragging conhost (18×/boot window) — inherent
  to session startup, minor.

## Related affinity change (same approval batch)

`channel_affinity_setting.switch_on_success` flipped false→true at ~00:15
(PUT /api/option/, DB readback verified; options sync ≤60s). Effect: when a
pinned channel fails and a retry succeeds elsewhere, the affinity record
migrates to the healthy channel — pinned conversations escape a degrading
channel after one successful failover instead of being re-nailed for the
full 300s TTL. See `docs/ops/sol-chain-muyuan-degradation-2026-08-16.md`.

## Rollback / monitoring

- vbs: previous behavior had no persisted state; revert = restore fixed-5s
  loop (not recommended).
- If the tunnel flaps again: check `ssh lima "ss -ltnp | grep :3000"` for a
  stale forward first — that is the recurring failure mode after any local
  reboot (server-side TCP timeout lags minutes behind).
- popup-hunt.log remains the ground truth for window flashes.
