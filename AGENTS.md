# Repository Guidelines

## Project Overview

CC Switch is a cross-platform Tauri 2 desktop app for managing AI coding tool configuration across Claude Code, Claude Desktop, Codex, Grok Build, OpenCode, OpenClaw, Kimi Code, Reasonix, and Pi.

Core purpose:
- One-click provider import/switching and tray quick switch.
- Local proxy/routing with provider failover, format conversion, and usage logging.
- MCP, prompts, skills, sessions, provider settings, cost/usage, cloud sync, and app-specific live config management.

Persistent data is centered on `~/.cc-switch/cc-switch.db` plus `~/.cc-switch/settings.json`; app live config projections live under tool-specific homes such as `~/.claude`, `~/.codex`, `~/.grok`, and `~/.kimi-code`.

## Architecture & Data Flow

High-level shape:
- `src/`: React 18 + TypeScript renderer. UI, hooks, typed Tauri API wrappers, React Query cache/mutations, form/view state.
- `src-tauri/src/`: Rust/Tauri backend. App startup, Tauri commands, SQLite persistence, provider/live-config services, proxy server, MCP/skills/prompts/session/usage services.
- `tests/` and `src-tauri/tests/`: frontend Vitest/MSW tests and Rust integration tests.

Runtime flow:
1. `src/main.tsx` creates the React root, installs `QueryClientProvider`, theme/update providers, toaster, and handles backend init errors.
2. `src/App.tsx` owns the main shell: active app/view, dialogs, selected providers, event listeners, env-conflict banners, and React Query invalidation.
3. UI/hooks call typed wrappers in `src/lib/api/*.ts`; wrappers call Tauri `invoke()` commands.
4. Rust commands in `src-tauri/src/commands/` parse/validate inputs and delegate to services.
5. Services under `src-tauri/src/services/` update SQLite SSOT and project app-specific live config files.
6. Local proxy flow: CLI tool -> `127.0.0.1:15721` -> Rust proxy router/failover/adapter/forwarder -> upstream provider -> transformed/logged response.

Backend startup in `src-tauri/src/lib.rs` installs Tauri plugins, initializes logging, opens/migrates SQLite, imports/backfills providers and MCP/prompts/skills, starts tray/sync/usage workers, recovers proxy takeover state, and registers all command handlers.

Important invariants:
- SQLite is the SSOT for providers, MCP, prompts, skills, usage, and settings-like app data. Live config files are projections/backfill sources.
- Provider changes must route through `ProviderService`; bypassing it can skip live projection, backups, current-provider state, tray refresh, proxy hot-switch, or MCP sync.
- Proxy takeover owns live app configs. Respect `SwitchLockManager`/takeover backup paths; do not write those files directly during takeover.
- Additive apps differ from exclusive apps: OpenCode/OpenClaw have no current provider; Kimi/Reasonix/Pi are additive but still keep current SSOT for proxy/UI routing.
- Schema changes are high impact. `src-tauri/src/database/schema.rs` and `SCHEMA_VERSION` migrations must stay aligned.

## Key Directories

- `src/components/` — feature UI panels and shadcn/Radix-style UI primitives. Domains include providers, settings, proxy, MCP, skills, usage, sessions, OpenClaw, env, and dialogs.
- `src/hooks/` — frontend orchestration hooks such as `useProviderActions`, `useProxyStatus`, `useSettingsForm`, `useTauriEvent`, and usage cache bridging.
- `src/lib/api/` — typed frontend wrappers around Tauri commands. Prefer these over raw `invoke()`.
- `src/lib/query/` — React Query client, query keys/hooks, mutations, usage cache patterns.
- `src/config/` — provider presets and app config used by UI/forms.
- `src/types.ts`, `src/types/` — frontend contracts mirrored from Rust provider/settings/proxy/usage shapes.
- `src-tauri/src/commands/` — thin command boundary; keep business logic in services.
- `src-tauri/src/services/` — provider, proxy, MCP, skills, profiles, prompts, sync, usage/session imports, env, model fetch, speed-test logic.
- `src-tauri/src/database/` — SQLite connection, schema migrations, backups, DAO modules.
- `src-tauri/src/proxy/` — Axum/hyper proxy server, provider router, adapters, streaming transforms, circuit breaker/failover, usage logging.
- `src-tauri/src/*_config.rs` — app-specific live config readers/writers.
- `tests/` — frontend Vitest tests and MSW/Tauri mocks.
- `src-tauri/tests/` — Rust integration tests using isolated temp HOME and real service APIs.
- `docs/ops/` — local NewAPI/DX ops policy and historical runbooks; verify current docs before treating scripts as live.
- `scripts/` — maintenance/smoke scripts; `scripts/ops/` contains NewAPI/VPS operational mirrors, several historical.
- `.trellis/` — Trellis task/spec/workflow context. Read relevant specs before code changes.

## Development Commands

Use pnpm from the repo root unless noted.

```bash
pnpm dev              # Tauri dev app; starts Vite renderer on port 3000
pnpm build            # Tauri production build
pnpm dev:renderer     # Vite renderer only
pnpm build:renderer   # Build renderer to dist/
pnpm typecheck        # tsc --noEmit
pnpm format           # Prettier over src/**/*.{js,jsx,ts,tsx,css,json}
pnpm format:check     # Prettier check over the same src glob
pnpm test:unit        # Vitest run
pnpm test:unit:watch  # Vitest watch
```

Rust/Tauri backend checks from repo root:

```bash
cargo fmt --check --manifest-path src-tauri/Cargo.toml
cargo clippy --manifest-path src-tauri/Cargo.toml -- -D warnings
cargo test --manifest-path src-tauri/Cargo.toml
cargo test --manifest-path src-tauri/Cargo.toml --features test-hooks
```

Notes:
- No `lint` script or ESLint config is present in `package.json`; some docs mention `pnpm lint`, but the manifest does not define it.
- `pnpm format` does not cover tests or root config files.
- Release/build workflows are heavy and signing-sensitive; do not run Tauri/Cargo rebuilds for routine NewAPI/DX ops.

## Code Conventions & Common Patterns

Frontend:
- TypeScript is strict. Avoid `any`, unused locals/params, and switch fallthrough.
- Use `@/...` imports for renderer/test aliases where existing code does.
- Components are functional React components with hooks.
- Use React Query for server/backend state; use local `useState` for view/form/dialog state only.
- Query keys are plain arrays, e.g. `["providers", appId]`, `["settings"]`, `["proxyStatus"]`, and `usageKeys.*`.
- Mutations invalidate explicit affected keys and often refresh tray/menu state best-effort.
- Use `src/lib/api/*.ts` wrappers instead of raw `invoke()` unless matching an existing bootstrap/edge-case pattern.
- Use `useTauriEvent` for Tauri event listen/unlisten lifecycle.
- Frontend errors commonly catch `unknown`, log with `console.error`/`console.warn`, extract messages via `extractErrorMessage`, and show `sonner` toasts with i18n defaults.
- UI styling follows Tailwind + CSS-variable tokens, shadcn/Radix components, lucide icons, and `.dark` selector mode.

Rust/backend:
- Commands should stay thin: parse inputs, get `State<'_, AppState>`, call services/DB/config modules, return serializable results.
- Command names/payloads are frontend contracts. Preserve camelCase compatibility via `#[tauri::command(rename_all = "camelCase")]` or explicit serde renames where used.
- Prefer `Result<T, String>` or `Result<T, AppError>` at command boundaries; `AppError` lives in `src-tauri/src/error.rs`.
- DB access goes through `Database` methods and the `lock_conn!` macro; avoid direct connection/unwrap patterns.
- Blocking filesystem/DB/session scans use `tauri::async_runtime::spawn_blocking` or background tasks where existing code does.
- Shared state uses `Arc`, `Mutex`, `tokio::sync::RwLock`, `OnceLock`, and Tauri managed state (`AppState`, auth states, service states).
- Preserve comments around platform-specific WebKit/Linux behavior and Windows/macOS Tauri setup unless replacing the behavior.

Project-level rules:
- Before changing config constants, payload fields, command names, app types, or schema values, search all callsites and update every layer.
- For new `AppType`/app integrations, update exhaustive matches: `as_str`, `FromStr`, `all`, additive-mode flags, DB CHECK/seed/migration, proxy ingress, UI visible-app state, and tests.
- Trellis specs live under `.trellis/spec/`; current backend spec calls out additive app config, takeover backup behavior, and fork migrations without upstream schema bumps.

## Important Files

- `package.json` — pnpm scripts, frontend deps, Tauri CLI version.
- `.node-version` — local Node pin (`22.12.0`).
- `pnpm-lock.yaml`, `pnpm-workspace.yaml` — pnpm lock/workspace discipline; root-only workspace.
- `vite.config.ts` — Vite root is `src`, dev server port `3000`, `@` alias, renderer output `dist/`.
- `vitest.config.ts` — jsdom setup, test include glob, coverage reporters.
- `tsconfig.json`, `tsconfig.node.json` — strict TS and path alias config.
- `tailwind.config.cjs`, `postcss.config.cjs`, `components.json`, `src/index.css` — UI token/style system.
- `src/main.tsx` — frontend bootstrap and database-upgrade/init-error branch.
- `src/App.tsx` — main UI state, app/view routing, backend event subscriptions.
- `src/lib/api/index.ts`, `providers.ts`, `settings.ts`, `proxy.ts`, `mcp.ts`, `usage.ts` — typed Tauri API surface.
- `src/lib/query/queryClient.ts`, `queries.ts`, `mutations.ts`, `usage.ts` — React Query defaults and core hooks.
- `src/hooks/useProviderActions.ts`, `useProxyStatus.ts`, `useTauriEvent.ts`, `useUsageCacheBridge.ts` — common frontend side-effect patterns.
- `src-tauri/Cargo.toml` — Rust crate, features, deps, release profile.
- `rust-toolchain.toml` — Rust channel `1.95`, `rustfmt`, `clippy`.
- `src-tauri/tauri.conf.json` — Tauri app/bundle/dev URL/deep-link/updater config.
- `src-tauri/src/main.rs`, `src-tauri/src/lib.rs` — Rust entry/setup/command registration.
- `src-tauri/src/store.rs` — shared backend dependency container.
- `src-tauri/src/app_config.rs`, `provider.rs` — app identity and provider contracts.
- `src-tauri/src/services/provider/mod.rs`, `services/proxy.rs` — highest-impact provider/proxy business logic.
- `src-tauri/src/proxy/server.rs`, `handlers.rs`, `forwarder.rs`, `provider_router.rs` — proxy request pipeline.
- `src-tauri/src/database/mod.rs`, `database/schema.rs` — SQLite init, migrations, schema version.
- `docs/ops/do-not-modify-cc-switch.md` — binding local ops constraint.
- `CONTRIBUTING.md`, `SECURITY.md`, `SUPPORT.md` — contribution, security, support expectations.

## Runtime/Tooling Preferences

- Use Node + pnpm for frontend/Tauri workflows. Do not switch to Bun/npm/yarn.
- Local Node is pinned by `.node-version` to `22.12.0`; CI currently uses Node 20, so avoid Node-22-only assumptions in committed scripts.
- Package manager is pnpm; lockfile is pnpm lockfile v9. CI pins pnpm `10.12.3`.
- Rust local toolchain is `1.95`; Cargo manifest minimum is `1.85.0`; CI uses stable with rustfmt/clippy.
- Tauri is v2.8. Renderer env exposure is limited to `VITE_` and `TAURI_` prefixes.
- Daily NewAPI / Claude DX work: do not modify or reinstall cc-switch; no `src-tauri/` changes for ops fixes, no Cargo/Tauri rebuild, no executable swap, no `~/.cc-switch/cc-switch.db` schema bump. Prefer NewAPI channels/abilities/model mappings/weights/param overrides, provider env, and `~/.claude/settings.json`.
- Current local proxy default is `127.0.0.1:15721`; address/port changes require stopping the proxy first.
- Modern Kimi Code uses `~/.kimi-code/config.toml` and `KIMI_CODE_HOME`; do not mix it with old Python `kimi-cli` config (`~/.kimi/config.toml`, `KIMI_SHARE_DIR`).

## Production Operations Safety

- Treat the OMP → local proxy → NewAPI → upstream provider → Guardian chain as production-critical. Before changing routing, model mappings, fallback chains, proxy supervision, or auto-fix behavior, capture the current configuration, process/port state, relevant logs, and a rollback artifact.
- Use a conservative change loop: reproduce or establish the failure first, make the smallest root-cause change, run the narrow route/proxy/Guardian smoke test, then run the relevant regression suite. Do not batch unrelated repairs or refactor stable paths during an incident.
- Do not modify the globally installed OMP package, replace executables, rebuild/reinstall CC Switch, alter `~/.cc-switch/cc-switch.db`, or edit live production configuration blindly. Prefer repository-owned gates and documented NewAPI/provider configuration changes; any exception requires explicit user authorization and a verified rollback path.
- Never infer health from a single probe. Cross-check configuration, process ownership, listening ports, request/response behavior, and logs; distinguish DNS/TCP/TLS, gateway, provider, model, conversion, and tool-call failures.
- Never claim end-to-end success from a partial check. Report exactly which route, model, transport, stream mode, fallback path, and duration were exercised; report failures and unavailable checks explicitly.
- Protect secrets: inspect only key names or redacted metadata, never print tokens, API keys, cookies, or full secret files into logs, tool output, commits, or documentation.
- Preserve a known-good backup before edits. Verify backup existence, size/time metadata, and content boundaries independently; do not delete, overwrite, or rotate user configuration without an explicit recovery plan.
- Avoid automatic retry and restart storms. Every supervisor/watchdog must have single-instance ownership, bounded backoff, duplicate-process prevention, clear health semantics, and a deterministic shutdown/recovery path.
- For fallback changes, validate model/provider resolution, remove primary-model repetition, detect orphan selectors, bound concurrency, and verify sibling workers do not stampede the same fallback target.
- Treat silent failure as a defect. Invalid configuration, dropped MCP/session entries, unsupported provider capabilities, migration conflicts, and disabled tools must produce actionable diagnostics or a deliberate fail-closed result.

## Testing & QA

Frontend:
- Tests are Vitest + jsdom + React Testing Library + MSW under `tests/**/*.{test,spec}.{ts,tsx}`.
- Global setup: `tests/setupGlobals.ts` then `tests/setupTests.ts`.
- MSW/Tauri mocks route `invoke()` as POSTs to `http://tauri.local/<command>` via `tests/msw/handlers.ts`.
- Shared frontend fixture state lives in `tests/msw/state.ts` and resets after each test.
- Tauri events are tested with `emitTauriEvent()` from `tests/msw/tauriMocks.ts`.
- Reuse `tests/utils/testQueryClient.ts` or disable React Query retries manually in hook/component tests.
- Prefer MSW handler overrides with `server.use(...)` over ad hoc raw `invoke` mocks.

Rust/Tauri:
- Integration tests live in `src-tauri/tests/` and use real library APIs plus test hooks.
- `src-tauri/tests/support.rs` creates isolated temp HOME via `CC_SWITCH_TEST_HOME`, `HOME`, and Windows `USERPROFILE`; tests that mutate env/files should lock `test_mutex()` and call `reset_test_fs()`.
- Preserve explicit assertions that user live config files are backed up, preserved, or not rewritten unexpectedly.

QA expectations:
- For frontend changes, run the narrow Vitest target if possible, then `pnpm typecheck` when contracts changed.
- For Rust/service/proxy changes, run the relevant `cargo test --manifest-path src-tauri/Cargo.toml <test_name>` first; use full Cargo checks before handoff when behavior is broad.
- For proxy changes, smoke the specific route/adapter/streaming transform/failover path; UI-only checks do not prove proxy behavior.
- For user-facing strings, update locale files consistently.
- Security issues go through GitHub Security Advisories, not public issues.
