# PersonalAI Governance — FREQUENT (daily, cheap): drift/capability/static/secret/rpo.
$ErrorActionPreference = 'Continue'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$rc = 0
& python "$repo\scripts\aic\aic.py" validate | Out-Null; if ($LASTEXITCODE -ne 0) { $rc = 1 }
foreach ($t in 'dsh','codex','claude','gemini','switchboard') {
  & python "$repo\scripts\aic\aic.py" diff $t | Out-Null
  if ($LASTEXITCODE -ne 0) { Write-Host "DRIFT: $t"; $rc = 1 }
}
foreach ($m in 'capability_gov','static_gov','routing_gov') {
  & python "$repo\scripts\governance\$m.py" | Out-Null
  if ($LASTEXITCODE -ne 0) { Write-Host "GOV-FINDING: $m"; $rc = 1 }
}
& python "$repo\scripts\durability\rpo_check.py" | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "GOV-FINDING: rpo_check"; $rc = 1 }
& python "$repo\scripts\governance\gov_status.py"
if ($LASTEXITCODE -ne 0) { $rc = 1 }
exit $rc
