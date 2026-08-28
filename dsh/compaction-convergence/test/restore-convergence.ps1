# Rollback the dsh-compaction-convergence overlay: restore the pristine upstream
# package captured at install time. Idempotent; no-op if no backup exists.
param(
  [string]$Checkout = ""
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$backupDir = Join-Path $repo ".dsh-convergence-backup\dsh-compaction-basic-upstream"

if (-not $Checkout) {
  $candidates = Get-ChildItem "$env:LOCALAPPDATA\npm-cache\_npx\*\node_modules\@deepseek-ai\dsh\package.json" -ErrorAction SilentlyContinue |
    ForEach-Object { Split-Path (Split-Path (Split-Path $_.FullName -Parent) -Parent) -Parent }
  $Checkout = $candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
}
if (-not $Checkout -or -not (Test-Path (Join-Path $Checkout "node_modules\@deepseek-ai\dsh\package.json"))) {
  throw "DSH checkout not found; pass -Checkout explicitly"
}
$target = Join-Path $Checkout "node_modules\@deepseek-ai\dsh-compaction-basic"
if (-not (Test-Path (Join-Path $backupDir "package.json"))) {
  throw "no upstream backup found at $backupDir — nothing to restore"
}
Remove-Item -Recurse -Force (Join-Path $target "*")
Copy-Item -Recurse -Force (Join-Path $backupDir "*") $target
$markerPath = Join-Path $target ".dsh-convergence.json"
if (Test-Path $markerPath) { Remove-Item -Force $markerPath }
Write-Host "restored upstream dsh-compaction-basic from $backupDir"
Write-Host "NOTE: restart the DSH host process (web GUI) to load the upstream module again."