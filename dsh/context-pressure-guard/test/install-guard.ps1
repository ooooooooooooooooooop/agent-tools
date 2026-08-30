# Install context-pressure guard overlays into the persistent DSH Web profile.
param([string]$Checkout = '')
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$profile = Join-Path $env:USERPROFILE '.dsh\profiles\web'
if (-not $Checkout) {
  $Checkout = Get-ChildItem "$env:LOCALAPPDATA\npm-cache\_npx\*\node_modules\@deepseek-ai\dsh\package.json" | ForEach-Object { Split-Path (Split-Path (Split-Path $_.FullName -Parent) -Parent) -Parent } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
}
if (-not $Checkout) { throw 'DSH checkout not found' }
$packages = @(
  @{ Source=$root; Name='dsh-agent-loop-pressure-guard' },
  @{ Source=(Join-Path $root 'token-meter'); Name='dsh-token-meter-pressure-guard' },
  @{ Source=(Join-Path $root 'tool-result-pruner'); Name='dsh-tool-result-pruner-pressure-guard' }
)
foreach ($p in $packages) {
  $dest = Join-Path $profile ("plugins\" + $p.Name)
  if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
  New-Item -ItemType Directory -Force $dest | Out-Null
  Copy-Item -Recurse -Force (Join-Path $p.Source 'lib') (Join-Path $dest 'lib')
  Copy-Item -Force (Join-Path $p.Source 'package.json') (Join-Path $dest 'package.json')
}
$link = Join-Path $profile 'node_modules\@deepseek-ai'
$probeFile = Join-Path $profile 'plugins\dsh-agent-loop-pressure-guard\lib\index.js'
$probe = "node -e ""const{createRequire}=require('module');const r=createRequire('$($probeFile -replace '\\','/')');r.resolve('@deepseek-ai/dsh-agent');r.resolve('@deepseek-ai/dsh-session');r.resolve('@deepseek-ai/dsh-llm')"""
& cmd /c $probe 2>$null
if ($LASTEXITCODE -ne 0) {
  if (Test-Path $link) { Remove-Item -Recurse -Force $link }
  New-Item -ItemType Junction -Path $link -Target (Join-Path $Checkout 'node_modules\@deepseek-ai') | Out-Null
}
$patch = Join-Path $profile 'cordis.patch.yml'
$bak = "$patch.context-pressure.bak"
if (-not (Test-Path $bak)) { Copy-Item $patch $bak }
$content = Get-Content $patch -Raw -Encoding UTF8
$start = '# DSH context-pressure guard overlay.'
if ($content -notmatch [regex]::Escape($start)) {
  $block = @"

$start
# Managed by dsh/context-pressure-guard/test/install-guard.ps1.
- id: token-meter
  disabled: true
- id: agent-loop
  disabled: true
- insert:
  - id: token-meter-pressure-guard
    name: './plugins/dsh-token-meter-pressure-guard/lib/index.js'
  - id: agent-loop-pressure-guard
    name: './plugins/dsh-agent-loop-pressure-guard/lib/index.js'
    config:
      contextAdmission:
        safetyMargin: 16384
        operationalMaxOutput: 65536
        inputMultiplier: 1.08
        routes:
          - provider: 'opencode-go'
            model: 'deepseek-v4-flash'
            providerAttestedLimit: 1048576
  - id: tool-result-pruner-pressure-guard
    name: './plugins/dsh-tool-result-pruner-pressure-guard/lib/index.js'
    disabled: true
"@
  [System.IO.File]::WriteAllText($patch, $content + $block, [System.Text.UTF8Encoding]::new($false))
}
Write-Host 'context-pressure guard installed; user-controlled DSH restart is required to load it.'
