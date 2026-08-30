param([string]$Checkout = '')
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$profile = Join-Path $env:USERPROFILE '.dsh\profiles\web'
$expected = @{
  'dsh-agent-loop-pressure-guard' = (Get-FileHash (Join-Path $root 'lib\index.js') -Algorithm SHA256).Hash
  'dsh-token-meter-pressure-guard' = (Get-FileHash (Join-Path $root 'token-meter\lib\index.js') -Algorithm SHA256).Hash
  'dsh-tool-result-pruner-pressure-guard' = (Get-FileHash (Join-Path $root 'tool-result-pruner\lib\index.js') -Algorithm SHA256).Hash
}
$errors=@()
foreach($name in $expected.Keys){$file=Join-Path $profile "plugins\$name\lib\index.js";if(-not(Test-Path $file)){$errors+="$name missing"}elseif((Get-FileHash $file -Algorithm SHA256).Hash -ne $expected[$name]){$errors+="$name hash mismatch"}}
$patch=Get-Content (Join-Path $profile 'cordis.patch.yml') -Raw -Encoding UTF8
foreach($needle in @('token-meter-pressure-guard','agent-loop-pressure-guard','providerAttestedLimit: 1048576','inputMultiplier: 1.08','operationalMaxOutput: 65536')){if($patch -notmatch [regex]::Escape($needle)){$errors+="patch missing $needle"}}
if($errors.Count){Write-Host "GUARD: FAIL — $($errors -join ', ')";exit 1}
Write-Host 'GUARD: VERIFY PASS — hashes and persistent profile wiring match.'
exit 0
