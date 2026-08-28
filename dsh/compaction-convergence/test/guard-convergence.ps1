# DSH compaction-convergence compatibility overlay — version exit gate.
# Idempotent; safe to run from bootstrap/lifecycle on any machine.
#
# Exit semantics:
#  0  VERIFY        affected version already patched and verified (or NOT_REQUIRED)
#  1  APPLY_NEEDED  affected version not patched (installer ran) — rerun to VERIFY
#  2  REVIEW        cannot prove compatibility with current version; do not patch blindly
param(
  [string]$Checkout = ""
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$forkRoot = Split-Path -Parent $here          # .../dsh/compaction-convergence
$FORK_LIB_HASH = "5bbf319ce8238b15b8952a8552e92cbaf55d8265b5e58889ee9157287e2300ec"

function Resolve-Checkout {
  if ($Checkout) { return $Checkout }
  $candidates = Get-ChildItem "$env:LOCALAPPDATA\npm-cache\_npx\*\node_modules\@deepseek-ai\dsh\package.json" -ErrorAction SilentlyContinue |
    ForEach-Object { Split-Path (Split-Path (Split-Path $_.FullName -Parent) -Parent) -Parent }
  $found = $candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $found -or -not (Test-Path (Join-Path $found "node_modules\@deepseek-ai\dsh\package.json"))) {
    throw "DSH checkout not found"
  }
  return $found
}
function HashOf($path) { (Get-FileHash $path -Algorithm SHA256).Hash.ToLower() }

$Checkout = Resolve-Checkout
$target = Join-Path $Checkout "node_modules\@deepseek-ai\dsh-compaction-basic"
$markerPath = Join-Path $target ".dsh-convergence.json"

if (-not (Test-Path (Join-Path $target "package.json"))) {
  Write-Host "GUARD: upstream package not installed; nothing to patch"
  exit 0
}
$pkg = Get-Content (Join-Path $target "package.json") -Raw | ConvertFrom-Json
$version = [string]$pkg.version
$libHash = HashOf (Join-Path $target "lib\index.js")

if ($version -eq "0.1.1-rc.2+conv.1") {
  $markerOk = Test-Path $markerPath
  $hashOk = $libHash -eq $FORK_LIB_HASH
  if ($markerOk -and $hashOk) {
    Write-Host "GUARD: VERIFY PASS — overlay active (version=$version hash=$libHash marker=present)"
    exit 0
  }
  Write-Host "GUARD: overlay claim present but evidence mismatch (hash=$libHash marker=$markerOk); reinstalling"
  & (Join-Path $here "install-convergence.ps1") -Checkout $Checkout
  Write-Host "GUARD: reinstalled — rerun to VERIFY"
  exit 1
}
if ($version -eq "0.1.1-rc.2") {
  Write-Host "GUARD: APPLY — affected version 0.1.1-rc.2 without overlay"
  & (Join-Path $here "install-convergence.ps1") -Checkout $Checkout
  Write-Host "GUARD: applied; rerun to VERIFY"
  exit 1
}

# Unknown/other version: detect whether upstream already contains both fixes
$lib = Get-Content (Join-Path $target "lib\index.js") -Raw
$hasCheckpointAware = $lib -match "isCompactCheckpointSource" -and $lib -match "failedPressureRegion"
$hasConvergence = $lib -match "summary is not smaller" -and $lib -match "failedPressureRegion"
if ($hasCheckpointAware -and $hasConvergence) {
  Write-Host "GUARD: NOT_REQUIRED — upstream $version already contains checkpoint-aware selection and pressure convergence"
  exit 0
}
Write-Host "GUARD: REVIEW — version $version; cannot prove upstream compatibility (checkpointAware=$hasCheckpointAware convergence=$hasConvergence)"
exit 2