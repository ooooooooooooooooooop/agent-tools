# dsh-compaction-convergence test bootstrap
# Creates a writable copy of the pinned fork inside the running DSH checkout so
# ESM bare imports (dsh-compaction, dsh-session, ...) resolve from the checkout
# node_modules, then runs the node:test suite against that copy.
param(
  [string]$Checkout = ""
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$forkSource = $repo

if (-not $Checkout) {
  $candidates = Get-ChildItem "$env:LOCALAPPDATA\npm-cache\_npx\*\node_modules\@deepseek-ai\dsh\package.json" -ErrorAction SilentlyContinue |
    ForEach-Object { Split-Path (Split-Path (Split-Path $_.FullName -Parent) -Parent) -Parent }
  $Checkout = $candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
}
if (-not $Checkout -or -not (Test-Path (Join-Path $Checkout "node_modules\@deepseek-ai\dsh\package.json"))) {
  throw "DSH checkout not found; pass -Checkout explicitly"
}
$target = Join-Path $Checkout "node_modules\@deepseek-ai\dsh-compaction-basic-convergence"
if (Test-Path $target) { Remove-Item -Recurse -Force $target }
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item -Recurse -Force (Join-Path $forkSource "*") $target
Get-Item (Join-Path $target "package.json") | Out-Null
$env:DSH_CHECKOUT = $Checkout
Write-Host "fork staged at $target"
& node --test (Join-Path $forkSource "test\convergence.test.mjs")
exit $LASTEXITCODE