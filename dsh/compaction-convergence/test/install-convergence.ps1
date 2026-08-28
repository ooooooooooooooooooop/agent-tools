# Deploy the pinned dsh-compaction-convergence fork over the running DSH checkout.
# This is a scripted, reversible overlay (not a manual npm-cache edit):
#   - backs up the installed upstream package next to the fork source,
#   - copies the fork in as the same package name (loader needs no change),
#   - writes an installation marker (source paths + SHA-256s) for audit,
#   - provide restore-convergence.ps1 to return to the backed-up upstream.
# Re-run after "npm install" replaces the checkout node_modules.
param(
  [string]$Checkout = ""
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$fork = $repo
$backupRoot = Join-Path $repo ".dsh-convergence-backup"

if (-not $Checkout) {
  $candidates = Get-ChildItem "$env:LOCALAPPDATA\npm-cache\_npx\*\node_modules\@deepseek-ai\dsh\package.json" -ErrorAction SilentlyContinue |
    ForEach-Object { Split-Path (Split-Path (Split-Path $_.FullName -Parent) -Parent) -Parent }
  $Checkout = $candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
}
if (-not $Checkout -or -not (Test-Path (Join-Path $Checkout "node_modules\@deepseek-ai\dsh\package.json"))) {
  throw "DSH checkout not found; pass -Checkout explicitly"
}

$target = Join-Path $Checkout "node_modules\@deepseek-ai\dsh-compaction-basic"
$backupDir = Join-Path $backupRoot "dsh-compaction-basic-upstream"
# 中断恢复：目标被清空但 backup 在 → 先还原上游再继续
if (-not (Test-Path (Join-Path $target "package.json")) -and (Test-Path (Join-Path $backupDir "package.json"))) {
  Copy-Item -Recurse -Force (Join-Path $backupDir "*") $target
  Write-Host "restored upstream from backup after interrupted state"
}
if (-not (Test-Path (Join-Path $target "package.json"))) {
  throw "upstream dsh-compaction-basic not found at $target"
}

# 1) backup upstream once (idempotent): keep the pristine copy for rollback.
$backupDir = Join-Path $backupRoot "dsh-compaction-basic-upstream"
if (-not (Test-Path (Join-Path $backupDir "package.json"))) {
  New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
  Copy-Item -Recurse -Force (Join-Path $target "*") $backupDir
  Write-Host "upstream backed up to $backupDir"
} else {
  Write-Host "upstream backup already exists at $backupDir"
}

# 2) capture marker hashes before overwrite
function Sha256Of($path) {
  (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
}
$upstreamHash = Sha256Of (Join-Path $target "lib\index.js")
$marker = @{
  schema_version = 1
  package = "@deepseek-ai/dsh-compaction-basic"
  deployed_version = "0.1.1-rc.2+conv.1"
  installed_at_utc = (Get-Date).ToUniversalTime().ToString("o")
  checkout = $Checkout
  fork_source = $fork
  upstream_lib_index_sha256 = $upstreamHash
  backup_dir = $backupDir
  backup_restore_cmd = "powershell -File $(Join-Path $PSScriptRoot 'restore-convergence.ps1') -Checkout `"$Checkout`""
}
$markerPath = Join-Path (Join-Path $repo "test") ".last-deploy-marker.json"
[System.IO.File]::WriteAllText($markerPath, ($marker | ConvertTo-Json -Depth 4))

# 3) overwrite upstream package with the fork (same package name so the loader
#    and cordis patch keep resolving it unchanged).
Remove-Item -Recurse -Force (Join-Path $target "*")
Copy-Item -Recurse -Force (Join-Path $fork "*") $target
$deployedHash = Sha256Of (Join-Path $target "lib\index.js")
Write-Host "deployed fork; fork lib/index.js sha256=$deployedHash"
Write-Host "marker written to $markerPath"

# 3b) checkout-internal marker (guard/verify source; travels with the package)
$deployedMarker = @{
  schema_version = 1
  package = "@deepseek-ai/dsh-compaction-basic"
  deployed_version = "0.1.1-rc.2+conv.1"
  installed_at_utc = (Get-Date).ToUniversalTime().ToString("o")
  fork_lib_index_sha256 = $deployedHash
  upstream_lib_index_sha256 = $upstreamHash
}
$deployedMarkerPath = Join-Path $target ".dsh-convergence.json"
[System.IO.File]::WriteAllText($deployedMarkerPath, ($deployedMarker | ConvertTo-Json -Depth 4))
Write-Host "checkout marker written to $deployedMarkerPath"
Write-Host ""
Write-Host "NOTE: restart the DSH host process (web GUI) so the new module is loaded."
Write-Host "Rollback: $(Join-Path $PSScriptRoot 'restore-convergence.ps1') -Checkout `"$Checkout`""
