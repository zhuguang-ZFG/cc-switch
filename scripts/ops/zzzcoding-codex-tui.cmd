@echo off
rem Interactive Codex TUI against api.zzzcoding.org gpt-6-astra. Codex-only gate.
rem Thin shim over zzzcoding-codex-tui.ps1 which reads the shared zzzcoding key file.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0zzzcoding-codex-tui.ps1" %*
