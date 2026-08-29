# Install the dsh-compaction-convergence overlay as a profile-local pinned package.
#
# Design (survives GUI restarts, which rebuild the npm-cache checkout):
#   1. fork -> <DSH_PROFILE>/plugins/dsh-compaction-convergence   (profile-owned, persistent)
#   2. dependency resolution: the fork imports @deepseek-ai/* from the checkout
#      node_modules through the profile's node_modules chain; when resolution is not
#      possible a junction is (re)created from <profile>/node_modules/@deepseek-ai ->
#      <checkout>/node_modules/@deepseek-ai (idempotent; rebuilt by guard on each
#      bootstrap because a GUI restart may leave an empty plain directory).
#   3. cordis.patch.yml gains an overlay row for the preset's compaction-basic id
#      (loader re-imports the patched module; original patch is backed up).
# Idempotent; reversible via restore-convergence.ps1; never edits the npm cache's
# canonical tarballs (checkout node_modules is only read, except the junction above).
param(
  [string]$Checkout = ""
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$overlay = Split-Path -Parent $here          # agent-tools/dsh/compaction-convergence
$profileRoot = Join-Path $env:USERPROFILE ".dsh\profiles\web"
$forkDest = Join-Path $profileRoot "plugins\dsh-compaction-convergence"

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

# 1) fork copy (canonical source stays in agent-tools; profile copy is executable)
New-Item -ItemType Directory -Force -Path $forkDest | Out-Null
Copy-Item -Recurse -Force (Join-Path $overlay "lib") (Join-Path $forkDest "lib")
Copy-Item -Force (Join-Path $overlay "package.json") (Join-Path $forkDest "package.json")
Copy-Item -Recurse -Force (Join-Path $overlay "test") (Join-Path $forkDest "test")
Remove-Item -Recurse -Force (Join-Path $forkDest "test\.replay"),(Join-Path $forkDest ".dsh-convergence-backup"),(Join-Path $forkDest "test\.last-deploy-marker.json") -ErrorAction SilentlyContinue

# 2) dependency resolution (junction fallback when the profile mirror is empty/absent)
$probe = "node -e ""const{createRequire}=require('module');const r=createRequire('$($forkDest -replace '\\','/')/lib/index.js');r.resolve('@deepseek-ai/dsh-compaction');r.resolve('@deepseek-ai/dsh-session');r.resolve('@deepseek-ai/dsh-llm')"""
$resolveOk = $true
try { & cmd /c $probe 2>$null; $resolveOk = $LASTEXITCODE -eq 0 } catch { $resolveOk = $false }
if (-not $resolveOk) {
  $link = Join-Path $profileRoot "node_modules\@deepseek-ai"
  if (Test-Path $link) { Remove-Item -Recurse -Force $link }
  New-Item -ItemType Junction -Path $link -Target (Join-Path $Checkout "node_modules\@deepseek-ai") | Out-Null
  Write-Host "dependency junction (re)created"
}

# 3) cordis patch overlay row (backup once, insert idempotently)
$patch = Join-Path $profileRoot "cordis.patch.yml"
$bak = Join-Path $profileRoot "cordis.patch.yml.dsh-conv.bak"
if (-not (Test-Path $bak)) { Copy-Item $patch $bak }
$content = Get-Content $patch -Raw -Encoding UTF8
if ($content -notmatch "compaction-basic-convergence") {
  $row = @"

# DSH compaction-convergence overlay (pinned local package at ./plugins/dsh-compaction-convergence).
# The upstream web bundle disables its host-plane compaction row, so keep that row disabled
# and mount the compatible local implementation under its own loader id. `insert` appends;
# reusing `compaction-basic` here would create a duplicate loader entry instead of an overlay.
# Managed by dsh/compaction-convergence/test/guard-convergence.ps1.
- id: compaction-basic
  disabled: true
- insert:
  - id: compaction-basic-convergence
    name: './plugins/dsh-compaction-convergence/lib/index.js'
    disabled: false
    config:
      summarizationProvider: 'cpa'
      summarizationModel: 'gemini-3.7-flash-high'
      maxTokens: 2048
      maxOverflowRetries: 2
"@
  [System.IO.File]::WriteAllText($patch, $content + $row)
}

# 4) markers
$forkHash = HashOf (Join-Path $forkDest "lib\index.js")
[System.IO.File]::WriteAllText(
  (Join-Path $forkDest "lib\.dsh-convergence.json"),
  (@{ schema_version = 1; deployed_version = "0.1.1-rc.2+conv.1"; installed_at_utc = (Get-Date).ToUniversalTime().ToString("o"); fork_lib_index_sha256 = $forkHash; checkout = $Checkout } | ConvertTo-Json -Depth 3)
)
[System.IO.File]::WriteAllText(
  (Join-Path $here ".last-deploy-marker.json"),
  (@{ deployed_at_utc = (Get-Date).ToUniversalTime().ToString("o"); fork_lib_index_sha256 = $forkHash; profile = $profileRoot } | ConvertTo-Json -Depth 3)
)
Write-Host "overlay installed at $forkDest (lib sha256=$forkHash)"
Write-Host "restart the DSH GUI process so the new module is loaded."