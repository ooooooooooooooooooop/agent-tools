param(
  [string]$ProfileRoot = $PSScriptRoot,
  [string]$NodePath = $env:DSH_NODE_PATH
)

$ErrorActionPreference = 'Stop'
$DshHome = Split-Path -Parent (Split-Path -Parent $ProfileRoot)
$statePath = Join-Path $ProfileRoot 'dsh-managed-state.json'
$manifestPath = Join-Path $ProfileRoot 'dsh-runtime-composition.json'

# Resolve the accepted managed composition from durable state, falling back to
# the composition manifest. No version string is hardcoded here.
$nodeRel = $null
$baseVersion = $null
$entryRel = 'node_modules\@deepseek-ai\dsh\lib\bin.js'
if (Test-Path -LiteralPath $statePath) {
  $st = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
  $nodeRel = $st.current.nodeRelativePath
  $baseVersion = $st.current.version
  if ($st.current.entryRelative) { $entryRel = $st.current.entryRelative }
} elseif (Test-Path -LiteralPath $manifestPath) {
  $m = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
  $nodeRel = $m.node.relativePath
  $baseVersion = $m.base.version
  $entryRel = $m.base.entryRelative
}
if ([string]::IsNullOrWhiteSpace($nodeRel) -or [string]::IsNullOrWhiteSpace($baseVersion)) {
  throw 'Managed composition state unavailable (dsh-managed-state.json / dsh-runtime-composition.json)'
}
$distributionRoot = Join-Path $ProfileRoot "base-dsh-$baseVersion"
$managedNodePath = Join-Path $DshHome $nodeRel
if ([string]::IsNullOrWhiteSpace($NodePath)) { $NodePath = $managedNodePath }
$entry = Join-Path $DshHome ($entryRel -replace '/', '\')
$packageJson = Join-Path $distributionRoot 'node_modules\@deepseek-ai\dsh\package.json'

if (!(Test-Path -LiteralPath $NodePath)) { throw "Managed Node runtime not found: $NodePath" }
if (!(Test-Path -LiteralPath $entry) -or !(Test-Path -LiteralPath $packageJson)) { throw "Pinned DSH distribution is incomplete: $distributionRoot" }
$nodeVersion = (& $NodePath --version).Trim()
if ($nodeVersion -notmatch '^v(22\.(?:19|2[0-9])|(?:2[4-9]|[3-9][0-9])\.)') { throw "Unsupported Node runtime $nodeVersion; require >=22.19.0 or >=24." }
$package = Get-Content -LiteralPath $packageJson -Raw | ConvertFrom-Json
if ($package.name -ne '@deepseek-ai/dsh' -or $package.version -ne $baseVersion) { throw "Pinned DSH package mismatch: $($package.name)@$($package.version) (expected @deepseek-ai/dsh@$baseVersion)" }

# Single-instance guard: refuse to start a second DSH Web host when one is
# already bound to the port. Two hosts sharing ~/.dsh/storages/workspace.json
# is the cross-process lost-update that produced the workspace-registry
# incident (DSH_WORKSPACE_REGISTRY_INTEGRITY, 2026-09-04): each process keeps
# its own in-memory view and republishes the whole file (last-write-wins, no
# cross-process lock). Fail closed instead of ever running two writers.
$portInUse = netstat -ano 2>$null | Select-String ':3080\s' | Select-String 'LISTENING'
if ($portInUse) {
  throw "Port 3080 is already in use by another DSH Web host; refusing to start a second instance (SINGLE_INSTANCE_GUARD)."
}

Set-Location -LiteralPath $ProfileRoot
& $NodePath $entry web --no-open
exit $LASTEXITCODE
