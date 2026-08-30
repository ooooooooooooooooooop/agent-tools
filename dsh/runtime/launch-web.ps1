param(
  [string]$ProfileRoot = $PSScriptRoot,
  [string]$NodePath = $env:DSH_NODE_PATH
)

$ErrorActionPreference = 'Stop'
$DshHome = Split-Path -Parent (Split-Path -Parent $ProfileRoot)
$distributionVersion = '0.1.1-rc.2'
$distributionRoot = Join-Path $ProfileRoot "base-dsh-$distributionVersion"
$entry = Join-Path $distributionRoot 'node_modules\@deepseek-ai\dsh\lib\bin.js'
$packageJson = Join-Path $distributionRoot 'node_modules\@deepseek-ai\dsh\package.json'
$managedNodePath = Join-Path $DshHome 'runtime\node-v22.19.0-win-x64\node.exe'

if ([string]::IsNullOrWhiteSpace($NodePath)) { $NodePath = $managedNodePath }
if (!(Test-Path -LiteralPath $NodePath)) { throw "Node runtime not found: $NodePath" }
if (!(Test-Path -LiteralPath $entry) -or !(Test-Path -LiteralPath $packageJson)) { throw "Pinned DSH distribution is incomplete: $distributionRoot" }

$nodeVersion = (& $NodePath --version).Trim()
if ($nodeVersion -notmatch '^v(22\.(?:19|2[0-9])|(?:2[4-9]|[3-9][0-9])\.)') { throw "Unsupported Node runtime $nodeVersion; require >=22.19.0 or >=24." }
$package = Get-Content -LiteralPath $packageJson -Raw | ConvertFrom-Json
if ($package.name -ne '@deepseek-ai/dsh' -or $package.version -ne $distributionVersion) { throw "Pinned DSH package mismatch: $($package.name)@$($package.version)" }

Set-Location -LiteralPath $ProfileRoot
& $NodePath $entry web --no-open
exit $LASTEXITCODE
