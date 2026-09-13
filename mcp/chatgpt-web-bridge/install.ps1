# ChatGPT Web Bridge - one-shot installer (vendored fork of Octo-Lex/ChatGPT-Web2API).
# powershell -ExecutionPolicy Bypass -File install.ps1
$root = $PSScriptRoot
# Venv resolution mirrors start.ps1: W2A_VENV override wins (keeps the runtime
# venv outside the repo — a .venv inside the package dir would be scanned by
# the repo validator), else the conventional package-local .venv.
$venvDir = if ($env:W2A_VENV) { $env:W2A_VENV } else { Join-Path $root ".venv" }
if (-not (Test-Path "$venvDir\Scripts\python.exe")) { python -m venv $venvDir }
& "$venvDir\Scripts\python.exe" -m pip install -e $root
if ($LASTEXITCODE -ne 0) { Write-Error "pip install failed"; exit 1 }

$cfgDir = Join-Path $env:USERPROFILE ".chatgpt_web2api"
New-Item -ItemType Directory -Force $cfgDir | Out-Null
$cfg = Join-Path $cfgDir "config.json"
if (-not (Test-Path $cfg)) {
    $json = @'
{ "parallel_tabs": true, "tab_mode": "owned", "mcp_session_pool_enabled": true,
  "mcp_session_pool_size": 3, "mcp_session_pool_ttl_seconds": 300,
  "request_pace_send_seconds": 30, "request_pace_read_seconds": 8,
  "request_pace_cooldown_seconds": 300 }
'@
    # BOM-less UTF-8: PS5.1's Set-Content -Encoding UTF8 emits a BOM that
    # breaks Python's json.load.
    [IO.File]::WriteAllText($cfg, $json, [Text.UTF8Encoding]::new($false))
    Write-Host "wrote $cfg"
}

Write-Host ""
Write-Host "Installed. Next:"
Write-Host "  1. Register MCP in your harness — stdio, harness-bound (recommended):"
Write-Host "     `"chatgpt-web`": { `"command`": `"$venvDir\Scripts\chatgpt-web2api-mcp.exe`" }"
Write-Host "     The MCP process lives/dies with the harness session; Chrome is"
Write-Host "     auto-launched on first tool call. Nothing runs when unused."
Write-Host "  2. Shared-daemon alternative: .\start.ps1 -> Chrome + REST:8080 + SSE:8090,"
Write-Host '     then register {"type":"sse","url":"http://127.0.0.1:8090/sse"}'
Write-Host "  3. Log into ChatGPT once in the spawned Chrome (profile persists)"
