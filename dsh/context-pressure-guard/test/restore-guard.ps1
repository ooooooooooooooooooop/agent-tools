param()
$ErrorActionPreference='Stop'
$profile=Join-Path $env:USERPROFILE '.dsh\profiles\web'
$patch=Join-Path $profile 'cordis.patch.yml'
$bak="$patch.context-pressure.bak"
if(Test-Path $bak){Copy-Item -Force $bak $patch;Remove-Item $bak -Force}
foreach($name in @('dsh-agent-loop-pressure-guard','dsh-token-meter-pressure-guard','dsh-tool-result-pruner-pressure-guard')){$dest=Join-Path $profile "plugins\$name";if(Test-Path $dest){Remove-Item -Recurse -Force $dest}}
Write-Host 'context-pressure guard restored; user-controlled restart required.'
