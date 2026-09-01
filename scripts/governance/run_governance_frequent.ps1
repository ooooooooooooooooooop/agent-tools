# PersonalAI Governance — FREQUENT (daily, cheap): drift/capability/static/secret/rpo.
$ErrorActionPreference = 'Continue'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$logDir = Join-Path $env:TEMP 'personalai-governance'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("frequent-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + ".log")
function Log([string]$msg) { $msg | Tee-Object -FilePath $log -Append }
Log "START frequent repo=$repo"
$rc = 0
& python "$repo\scripts\aic\aic.py" validate *>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { $rc = 1 }
foreach ($t in 'dsh','codex','claude','gemini','switchboard') {
  & python "$repo\scripts\aic\aic.py" diff $t *>&1 | Tee-Object -FilePath $log -Append
  if ($LASTEXITCODE -ne 0) { Log "DRIFT: $t"; $rc = 1 }
}
foreach ($m in 'capability_gov','static_gov','routing_gov') {
  & python "$repo\scripts\governance\$m.py" *>&1 | Tee-Object -FilePath $log -Append
  if ($LASTEXITCODE -ne 0) { Log "GOV-FINDING: $m"; $rc = 1 }
}
& python "$repo\scripts\durability\rpo_check.py" *>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { Log "GOV-FINDING: rpo_check"; $rc = 1 }
& python "$repo\scripts\governance\gov_status.py" *>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { $rc = 1 }
Log "END frequent rc=$rc log=$log"
exit $rc