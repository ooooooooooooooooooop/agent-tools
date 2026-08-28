# PersonalAI Governance — FREQUENT (daily, cheap): drift/capability/static/secret/rpo.
$ErrorActionPreference = 'Continue'
$repo = "C:\Users\admin\Desktop\skills"
$rc = 0
& python "$repo\scripts\aic\aic.py" validate | Out-Null; if ($LASTEXITCODE -ne 0) { $rc = 1 }
foreach ($t in 'dsh','codex','claude','gemini','switchboard') {
  & python "$repo\scripts\aic\aic.py" diff $t | Out-Null
  if ($LASTEXITCODE -ne 0) { Write-Host "DRIFT: $t"; $rc = 1 }
}
foreach ($m in 'capability_gov','static_gov','routing_gov') {
  & python "$repo\scripts\governance\$m.py" | Out-Null
  if ($LASTEXITCODE -ne 0) { Write-Host "GOV-FINDING: $m"; }
}
& python "$repo\scripts\durability\rpo_check.py" | Out-Null
& python "$repo\scripts\governance\gov_status.py"
exit $rc
