@echo off
rem Launch Claude Code against api.zzzcoding.org claude-opus-5 (direct, bypasses NewAPI)
claude --settings "%USERPROFILE%\.claude\zzzcoding-settings.json" %*
