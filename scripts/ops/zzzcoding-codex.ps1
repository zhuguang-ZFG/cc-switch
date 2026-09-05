$key = (Get-Content -Raw "$env:USERPROFILE\.claude\zzzcoding-settings.json" | ConvertFrom-Json).env.ANTHROPIC_API_KEY
if (-not $key) { Write-Error 'key file missing or empty'; exit 1 }
$env:ZZZCODING_API_KEY = $key.Trim()
codex exec -c model_provider=zzzcoding -c model=gpt-6-astra @Args
