# PersonalAI Governance — WEEKLY (less frequent): upstream discovery/review,
# model state/health, dead config, stale memory, duplicate rules, project state.
# Expensive paid-model canary stays manual. Discovery/proposals never auto-adopt.
$ErrorActionPreference = 'Continue'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$rc = 0
foreach ($m in 'upstream_capability_review','model_state','model_health','dead_config','memory_gov','dup_rules','project_state_gov','durability_gov') {
  & python "$repo\scripts\governance\$m.py"
  if ($LASTEXITCODE -ne 0) {
    Write-Host "GOV-FINDING: $m exit=$LASTEXITCODE"
    $rc = 1
  }
}
exit $rc
