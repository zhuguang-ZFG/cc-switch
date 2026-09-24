$sec = Get-Content -Raw "$env:USERPROFILE\.omp\guardian\secrets.json" | ConvertFrom-Json
$key = $sec.anyrouter_proxy_key
if (-not $key) { Write-Error 'anyrouter_proxy_key missing in secrets.json'; exit 1 }
$env:ANTHROPIC_BASE_URL = 'http://127.0.0.1:8789'
$env:ANTHROPIC_AUTH_TOKEN = $key.Trim()
claude --settings "$PSScriptRoot\claude-opus55-any.settings.json" --model claude-opus-5-5 @Args
