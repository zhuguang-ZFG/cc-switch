@echo off
rem Launch Claude Code against anyrouter claude-opus-5-5 via the local 8789 fingerprint bridge.
rem Thin shim over claude-opus55-any.ps1 which reads anyrouter_proxy_key from secrets.json.
rem Usage: claude-opus55-any.cmd [claude args]. 429 is congestion-style; the CLI's internal
rem retries squeeze in (verified 2026-09-23, ~27s). NewAPI 3002 cannot route this model
rem (ch72 governance-disabled, model unregistered).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0claude-opus55-any.ps1" %*
