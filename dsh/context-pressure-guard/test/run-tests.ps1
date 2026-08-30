$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$checkout = $env:DSH_CHECKOUT
if (-not $checkout) { throw 'DSH_CHECKOUT is required' }
$nm = Join-Path $checkout 'node_modules\@deepseek-ai'
$pairs = @(
  @{ Source = $root; Target = 'dsh-agent-loop-pressure-guard' },
  @{ Source = (Join-Path $root 'token-meter'); Target = 'dsh-token-meter-pressure-guard' },
  @{ Source = (Join-Path $root 'tool-result-pruner'); Target = 'dsh-tool-result-pruner-pressure-guard' },
  @{ Source = (Join-Path (Split-Path -Parent $root) 'compaction-convergence'); Target = 'dsh-compaction-basic-convergence' }
)
foreach ($pair in $pairs) {
  $target = Join-Path $nm $pair.Target
  if (Test-Path $target) { Remove-Item -Recurse -Force $target }
  New-Item -ItemType Directory -Force $target | Out-Null
  Copy-Item -Recurse -Force (Join-Path $pair.Source '*') $target
}
$lifecycleTarget = Join-Path $checkout 'web\plugins\dsh-context-lifecycle'
if (Test-Path $lifecycleTarget) { Remove-Item -Recurse -Force $lifecycleTarget }
New-Item -ItemType Directory -Force (Join-Path $lifecycleTarget 'lib') | Out-Null
Copy-Item -Force (Join-Path (Split-Path $root -Parent) 'context-lifecycle\lib\index.js') (Join-Path $lifecycleTarget 'lib\index.js')
$env:DSH_CHECKOUT = $checkout
& node --test (Join-Path $root 'test\pressure-guard.test.mjs') (Join-Path $root 'test\runtime-smoke.test.mjs') (Join-Path (Split-Path $root -Parent) 'context-lifecycle\test\lifecycle.test.mjs')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& powershell -ExecutionPolicy Bypass -File (Join-Path (Split-Path -Parent $root) 'compaction-convergence\test\run-tests.ps1') -Checkout $checkout
exit $LASTEXITCODE
