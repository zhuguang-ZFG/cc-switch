# OMP plugins, MCP, and skills hardening (2026-08-17)

## Scope and decision

This change hardens the local OMP extensibility surface without changing model
providers, model roles, fallback chains, NewAPI channels, or credentials. The
live OMP version was `17.3.5`, which was the latest stable release at the time
of the review.

OMP upstream: <https://github.com/can1357/oh-my-pi>.

The applied posture is:

- upgrade `pi-hermes-memory` from `0.9.5` to `0.9.6`;
- keep `omp-cache-optimizer` at `1.2.3` and `omp-model-profile` at `0.2.4`;
- reduce the default skill catalog from 59 discovered skills to a 26-skill
  allowlist without deleting any source skill;
- disable project-root MCP configuration discovery by default while retaining
  user-level MCP configuration and other user-level discovery sources;
- change the default tool approval mode from `yolo` to `write`.

No model request was sent during implementation or verification. The existing
`omp -c` process was not stopped or replaced.

## Rollback snapshot

Before the first write, a rollback directory was created at:

```text
C:\Users\zhugu\.omp\extension-backups\pi-hermes-memory-0.9.5-pre-0.9.6-20260817-233743
```

It contains the pre-upgrade plugin `package.json`, `bun.lock`,
`omp-plugins.lock.json`, `hermes-memory-config.json`, the Hermes memory text
files and generated skills, a pre-hardening `config.yml`, and an online SQLite
backup of `sessions.db`.

The online backup was necessary because an OMP session was active. It produced
a consistent 151,875,584-byte database; `PRAGMA integrity_check` returned
`ok`. The retained SHA-256 values are:

| Artifact | SHA-256 |
| --- | --- |
| `omp-plugins.lock.json` | `B8620A37D779EFF549DE5D3665FA4912363B8A93A0779407B8858ADC986A2D04` |
| `sessions.db` | `D9635ABAAC864981830E58C0A08BAD6639DDC44C35D8B6EAA38C788ED614A503` |

The config rollback file is
`config.yml.before-skills-mcp-hardening` (3,267 bytes). Do not restore the
database or config over a running OMP process. Close OMP normally first and
take another snapshot of the post-change data before any rollback.

## Hermes upgrade

`pi-hermes-memory@0.9.6` was installed through OMP's npm plugin manager. This
release is relevant to the existing approximately 145 MiB session database
because it:

- stops indexing oversized `tool_result` content;
- limits an indexed message to 100 KiB;
- defers the expensive startup integrity check for large databases;
- makes memory-full results directly actionable.

Release: <https://github.com/chandra447/pi-hermes-memory/releases/tag/v0.9.6>.

The installed package manifest, OMP plugin list, plugin lock, and plugin
doctor all reported `0.9.6`. `omp plugin doctor --json` reported every plugin
and plugin-directory check as `ok`. The live Hermes `sessions.db` also passed
`PRAGMA quick_check` after the upgrade.

The background model remains the separately configured
`zg-newapi/agnes-2.5-flash` with thinking disabled. Plugin installation did not
change that override. The OMP process that was already running retained the
extension code loaded at its original start time; a later normal OMP start is
required to load `0.9.6`.

## Skill allowlist

OMP's own `loadSkills()` discovery implementation was used for both sides of
the comparison. The baseline was 59 skills with zero warnings:

| Source | Skills |
| --- | ---: |
| `agents:user` | 22 |
| `claude:user` | 18 |
| `claude-plugins:user` | 19 |

Their names and descriptions contributed 16,003 characters of catalog
metadata. The following persisted `skills.includeSkills` patterns resolve to
26 skills and 7,367 metadata characters, a reduction of approximately 54%:

```json
[
  "trellis-*",
  "smart-explore",
  "code-tour",
  "error-handling",
  "tdd-workflow",
  "verification-loop",
  "e2e-testing",
  "production-audit",
  "windows-desktop-e2e",
  "skill-scout",
  "skill-stocktake",
  "babysit",
  "council",
  "impeccable",
  "design-is"
]
```

The post-write read-only discovery returned exactly 26 skills with zero
warnings. Source files were left intact, so a task-specific session can still
override the filter with `omp --skills <patterns>` or the setting can be reset.
The default list intentionally excludes deprecated `continuous-learning` and
the overlapping `continuous-learning-v2`, `knowledge-agent`, `mem-search`, and
`learn-codebase` workflows. Hermes and Trellis remain the default memory and
project-workflow paths.

## MCP and approval boundary

The persisted settings are now:

```yaml
mcp:
  enableProjectConfig: false

tools:
  approvalMode: write
```

`mcp.enableProjectConfig=false` prevents a repository-root `.mcp.json` or
`mcp.json` from adding tools merely because OMP opened that repository. It does
not disable the user's `~/.omp/agent/mcp.json`; the explicitly enabled
`context7`, `github`, and `anysearch` entries remain available. A read-only
discovery check found 10 effective user or imported MCP entries and zero
project-level sources.

`tools.approvalMode=write` auto-approves read-only and workspace-write tools,
but prompts before execution-tier tools such as shell, browser, evaluation,
and task tools. A trusted one-off session can override this with
`--approval-mode yolo`; the safer default remains persistent.

OMP `17.3.5` still has upstream limitations that this local setting cannot
solve:

- non-restricted subagents can inherit MCP and extension tools:
  <https://github.com/can1357/oh-my-pi/issues/8599>;
- MCP auto-discovery cannot yet be disabled by Claude/OpenCode/Cursor source:
  <https://github.com/can1357/oh-my-pi/issues/8668>;
- skill sibling-file and explicit-path loading remain incomplete:
  <https://github.com/can1357/oh-my-pi/issues/8740> and
  <https://github.com/can1357/oh-my-pi/issues/8766>.

Do not compensate by adding more always-on MCP servers or extensions. OMP's
`xdev` schema deferral reduces prompt-schema cost, but it does not provide a
least-privilege boundary for inherited tools.

## Verification

The following checks passed after all writes:

- `omp plugin list --json`: Hermes `0.9.6`, cache optimizer `1.2.3`, model
  profile `0.2.4`;
- `omp plugin doctor --json`: all checks `ok`;
- `omp config get skills.includeSkills --json`: the 15 persisted patterns
  above;
- read-only effective skill discovery: 26 skills, 7,367 metadata characters,
  zero warnings;
- `omp config get mcp.enableProjectConfig --json`: `false`;
- read-only effective MCP discovery: 10 entries, zero project sources;
- `omp config get tools.approvalMode --json`: `write`;
- live Hermes `sessions.db`: `PRAGMA quick_check = ok`;
- the pre-existing OMP process remained responsive and was not terminated;
- repository worktree was clean before this documentation update.

These checks prove installation, persisted configuration, and read-only
discovery behavior. They do not claim a hot reload into the pre-existing OMP
session and do not claim an upstream model or MCP transport request.

## Rollback

For a config-only rollback, close OMP normally, restore
`config.yml.before-skills-mcp-hardening` to `~/.omp/agent/config.yml`, and start
OMP again. For a narrower rollback, reset only the affected keys:

```powershell
omp config reset skills.includeSkills
omp config set mcp.enableProjectConfig true
omp config set tools.approvalMode yolo
```

To downgrade only Hermes, close OMP normally and run:

```powershell
omp plugin install pi-hermes-memory@0.9.5
omp plugin doctor --json
```

Restore the retained `sessions.db` only if a confirmed data migration or
corruption requires it. Before doing so, preserve the newer database and use
an offline replacement while OMP is closed; ordinary plugin rollback should
not overwrite newer memory data.
