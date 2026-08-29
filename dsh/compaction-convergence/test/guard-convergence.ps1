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
$forkRoot = Split-Path -Parent $here          # agent-tools/dsh/compaction-convergence
$profileRoot = Join-Path $env:USERPROFILE ".dsh\profiles\web"
$forkDest = Join-Path $profileRoot "plugins\dsh-compaction-convergence"
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

# 1) upstream version gate
if (Test-Path (Join-Path $target "package.json")) {
  $pkg = Get-Content (Join-Path $target "package.json") -Raw | ConvertFrom-Json
  $version = [string]$pkg.version
  if ($version -ne "0.1.1-rc.2" -and $version -ne "0.1.1-rc.2+conv.1") {
    $lib = Get-Content (Join-Path $target "lib\index.js") -Raw
    $hasCheckpointAware = $lib -match "isCompactCheckpointSource" -and $lib -match "failedPressureRegion"
    $hasConvergence = $lib -match "summary is not smaller" -and $lib -match "failedPressureRegion"
    if ($hasCheckpointAware -and $hasConvergence) {
      Write-Host "GUARD: NOT_REQUIRED — upstream $version already contains checkpoint-aware selection and pressure convergence"
      exit 0
    }
    Write-Host "GUARD: REVIEW — upstream version $version; compatibility cannot be proven (checkpointAware=$hasCheckpointAware convergence=$hasConvergence)"
    exit 2
  }
  if ($version -eq "0.1.1-rc.2" -and -not (Test-Path $forkDest)) {
    Write-Host "GUARD: APPLY — affected version 0.1.1-rc.2 without overlay"
    & (Join-Path $here "install-convergence.ps1") -Checkout $Checkout
    Write-Host "GUARD: applied; rerun to VERIFY"
    exit 1
  }
} else {
  Write-Host "GUARD: REVIEW — no dsh-compaction-basic installed; cannot determine version"
  exit 2
}

# 2) profile overlay verification: fork present, hash matches, patch row wired,
#    dependency resolution works
$forkLib = Join-Path $forkDest "lib\index.js"
$errors = @()
if (-not (Test-Path $forkLib)) { $errors += "fork missing" }
elseif ((HashOf $forkLib) -ne $FORK_LIB_HASH) { $errors += "fork hash mismatch" }
$marker = Join-Path $forkDest "lib\.dsh-convergence.json"
if (-not (Test-Path $marker)) { $errors += "marker missing" }
$patch = Get-Content (Join-Path $profileRoot "cordis.patch.yml") -Raw -Encoding UTF8
if ($patch -notmatch "compaction-basic-convergence") { $errors += "patch conv id missing" }
if ($patch -notmatch "\./plugins/dsh-compaction-convergence/lib/index\.js") { $errors += "patch file ref missing" }
if ($patch -notmatch "(?m)^- id: compaction-basic`r?`n  disabled: true") { $errors += "patch upstream disable missing" }
$probe = "node -e ""const{createRequire}=require('module');const r=createRequire('$($forkDest -replace '\\','/')/lib/index.js');r.resolve('@deepseek-ai/dsh-compaction');r.resolve('@deepseek-ai/dsh-session');r.resolve('@deepseek-ai/dsh-llm')"""
try { & cmd /c $probe 2>$null; if ($LASTEXITCODE -ne 0) { $errors += "dependency resolution failed" } } catch { $errors += "dependency resolution failed" }

if ($errors.Count -eq 0) {
  Write-Host "GUARD: VERIFY PASS — profile overlay active (hash=$FORK_LIB_HASH marker=present patch=wired deps=resolvable)"
  exit 0
}
Write-Host "GUARD: evidence mismatch ($($errors -join ', ')); reinstalling"
& (Join-Path $here "install-convergence.ps1") -Checkout $Checkout
Write-Host "GUARD: reinstalled — rerun to VERIFY"
exit 1