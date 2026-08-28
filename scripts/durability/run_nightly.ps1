# PersonalAI Durability Nightly — orchestrator (Windows Task Scheduler target).
# Schedule decides WHEN; these tools decide WHAT. aic is not involved.
$ErrorActionPreference = 'Continue'
$repo = "C:\Users\admin\Desktop\skills"
$py = "python"
$rc = 0
foreach ($job in @('backup_sessions','backup_broker','backup_configs','check_repos','restore_check','rpo_check')) {
  & $py "$repo\scripts\durability\$job.py"
  $code = $LASTEXITCODE
  if ($job -eq 'check_repos' -and $code -eq 2) { $code = 0 }  # risk rows are warnings, not job failure
  if ($job -eq 'rpo_check' -and $code -ne 0) { Write-Host "RPO ATTENTION: exit=$code" }
  if ($code -ne 0 -and $job -ne 'rpo_check') { $rc = 1 }
}
exit $rc
