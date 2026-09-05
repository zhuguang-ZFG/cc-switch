@echo off
rem Launch Codex CLI against api.zzzcoding.org gpt-6-astra. Codex-only client gate.
rem Thin shim over zzzcoding-codex.ps1 which reads the shared zzzcoding key file.
rem Usage: zzzcoding-codex.cmd "prompt". History in models.yml zz-coding comment.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0zzzcoding-codex.ps1" %*
