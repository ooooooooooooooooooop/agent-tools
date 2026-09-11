# ChatGPT-Web2API launcher — persistent-Chrome topology.
#
#   start.ps1  launches Chrome itself (standalone, --start-minimized) so the
#   REST/MCP daemons always ATTACH to CDP :9222 and never own the browser.
#   Restarting or crashing daemons leaves Chrome (and your login) untouched.
#
# Idempotent: skips whatever is already healthy. Safe to re-run anytime.

# Venv resolution: W2A_VENV env override wins (lets the runtime venv live
# outside the repo — a .venv inside the package dir would be scanned by the
# repo validator), else the conventional package-local .venv.
$venv = if ($env:W2A_VENV) { Join-Path $env:W2A_VENV "Scripts" } else { Join-Path $PSScriptRoot ".venv\Scripts" }
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$profile = Join-Path $env:USERPROFILE ".chatgpt-web2api\chrome-profile"

# Expose destructive tools (delete_conversation / delete_memory / ...) to MCP
# clients. Single-user local box — remove this line to hard-lock them again.
$env:W2A_ENABLE_DESTRUCTIVE = "1"

function Test-Cdp { try { (Invoke-WebRequest -Uri "http://127.0.0.1:9222/json/version" -TimeoutSec 2 -UseBasicParsing).StatusCode -eq 200 } catch { $false } }
function Test-Port($p) { try { (Invoke-WebRequest -Uri "http://127.0.0.1:$p/" -TimeoutSec 2 -UseBasicParsing) | Out-Null; $true } catch { $_.Exception.Response -ne $null } }

# 1. Chrome — standalone, survives all daemon restarts
if (-not (Test-Cdp)) {
    Write-Host "Launching standalone Chrome (minimized)..."
    Start-Process -FilePath $chrome -ArgumentList @(
        "--remote-debugging-port=9222",
        "--user-data-dir=$profile",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-minimized",
        "https://chatgpt.com"
    )
    $ok = $false
    for ($i = 0; $i -lt 30; $i++) { Start-Sleep 1; if (Test-Cdp) { $ok = $true; break } }
    if (-not $ok) { Write-Error "Chrome CDP never came up on :9222"; exit 1 }
    Write-Host "Chrome CDP ready."
} else { Write-Host "Chrome CDP already up." }

# 2. REST daemon :8080 — attaches to Chrome, does not own it
if (-not (Test-Port 8080)) {
    Write-Host "Starting REST daemon..."
    Start-Process -WindowStyle Hidden -FilePath "$venv\chatgpt-web2api.exe" -WorkingDirectory $PSScriptRoot
} else { Write-Host "REST already up." }

# 3. MCP SSE :8090 — attaches too; pool materializes a tab per session
if (-not (Test-Port 8090)) {
    Write-Host "Starting MCP SSE server on :8090..."
    Start-Process -WindowStyle Hidden -FilePath "$venv\chatgpt-web2api-mcp.exe" -ArgumentList "--transport","sse","--port","8090" -WorkingDirectory $PSScriptRoot
} else { Write-Host "MCP SSE already up." }

Write-Host "Done. REST: http://127.0.0.1:8080/v1   MCP SSE: http://127.0.0.1:8090/sse"
