# Boot popup storm RCA + fixes (2026-08-17, continued 01:05)

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

## Round 2 (01:05): post-fix sweep + remaining boot flashers closed

Post-fix hunter-log analysis (00:18–00:50, 689 entries) required splitting
"process spawned" from "window shown": children of a console-owning parent
(terminal OMP sessions, build tools) attach to the parent's console and
never flash. After that filter:

- 42× `conhost --headless powershell ... .new-api-local\watchdog.ps1`
  (LocalNewAPI-Watchdog task, 1/min) — already headless, invisible. Not a
  popup source despite the log volume.
- MCP `.cmd`/`npx` chains (mcp-server-filesystem, rubber-duck, claude-mem,
  fz_mcp_server) spawn under interactive terminal sessions → attach to the
  terminal console → invisible in the normal workflow. Downgraded from
  "popup source" to "log noise" (they only flash if a session is launched
  detached, e.g. by a scheduler/GUI).
- The two real remaining boot flashers — both `powershell -WindowStyle
  Hidden` from consoleless parents, which creates-then-hides a console
  (classic scheduled-task flash) — wrapped in `conhost --headless`:
  1. Scheduled task **NewAPI Guardian Watchdog** action
  2. Registry `HKCU\...\Run\Local NewAPI` (start.ps1)
  Both take effect next logon. Wrapped watchdog launch verified working
  (persistent 30s loop, duplicate-guard intact, guardian heartbeat fresh).
- Clash Verge checked: `enable_silent_start: true` already — no window.

**Expected boot popup count after this round: ~0.** Definitive test is the
next reboot; popup-hunt.log + a manual look at the desktop will confirm.
