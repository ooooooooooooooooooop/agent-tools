# ChatGPT Web Bridge - one-shot installer (vendored fork of Octo-Lex/ChatGPT-Web2API).
# powershell -ExecutionPolicy Bypass -File install.ps1
$root = $PSScriptRoot
python -m venv "$root\.venv"
& "$root\.venv\Scripts\python.exe" -m pip install -e $root
if ($LASTEXITCODE -ne 0) { Write-Error "pip install failed"; exit 1 }

$cfgDir = Join-Path $env:USERPROFILE ".chatgpt_web2api"
New-Item -ItemType Directory -Force $cfgDir | Out-Null
$cfg = Join-Path $cfgDir "config.json"
if (-not (Test-Path $cfg)) {
    @'
{ "parallel_tabs": true, "tab_mode": "owned", "mcp_session_pool_enabled": true,
  "mcp_session_pool_size": 3, "mcp_session_pool_ttl_seconds": 300,
  "request_pace_send_seconds": 30, "request_pace_read_seconds": 8,
  "request_pace_cooldown_seconds": 300 }
'@ | Set-Content -Encoding UTF8 $cfg
    Write-Host "wrote $cfg"
}

Write-Host ""
Write-Host "Installed. Next:"
Write-Host "  1. .\start.ps1            -> Chrome + REST:8080 + MCP:8090"
Write-Host "  2. Log into ChatGPT once in the spawned Chrome (profile persists)"
Write-Host '  3. MCP registration:      "chatgpt-web": {"type":"sse","url":"http://127.0.0.1:8090/sse"}'
