param(
  [switch]$CheckOnly
)

# Idempotently register the existing Personal AI governance/durability/sync-check
# runners with the Windows Task Scheduler. This is deployment/recovery glue, not a
# new scheduler. Canonical contracts: run_governance_*.ps1 (governance),
# run_nightly.ps1 (durability), run_sync_check.ps1 (sync check-only adapter).
$ErrorActionPreference = 'Stop'
$canonicalRoot = 'C:\Desktop\skills'
$canonicalRootResolved = (Resolve-Path -LiteralPath $canonicalRoot).Path
$scriptRootResolved = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$expectedScriptPath = Join-Path $canonicalRootResolved 'scripts\governance'
$expectedScriptRoot = (Resolve-Path -LiteralPath $expectedScriptPath).Path
$canonicalSourceMatches = [StringComparer]::OrdinalIgnoreCase.Equals(
  $scriptRootResolved.TrimEnd('\'), $expectedScriptRoot.TrimEnd('\')
)
if (-not $canonicalSourceMatches) {
  throw "Refusing non-canonical registration source: $scriptRootResolved (expected $expectedScriptRoot)"
}
$repo = $canonicalRootResolved
$powerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$specs = @(
  @{
    Name = 'PersonalAI-Governance-Frequent'
    Runner = Join-Path $PSScriptRoot 'run_governance_frequent.ps1'
    Trigger = New-ScheduledTaskTrigger -Daily -At '04:00'
    Description = 'Daily Personal AI drift, capability, static, routing, RPO, and status checks.'
  },
  @{
    Name = 'PersonalAI-Governance-Weekly'
    Runner = Join-Path $PSScriptRoot 'run_governance_weekly.ps1'
    Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '04:45'
    Description = 'Weekly proposal-only upstream capability discovery and deeper Personal AI governance.'
  },
  @{
    Name = 'PersonalAI-Durability-Nightly'
    Runner = Join-Path $repo 'scripts\durability\run_nightly.ps1'
    Trigger = New-ScheduledTaskTrigger -Daily -At '03:30'
    Description = 'Nightly Personal AI backup, repo durability, restore-check, and RPO checks.'
  },
  @{
    Name = 'PersonalAI-Sync-Check'
    Runner = Join-Path $repo 'scripts\governance\run_sync_check.ps1'
    Trigger = New-ScheduledTaskTrigger -Daily -At '09:00'
    Description = 'Check-only Personal AI sync classification (never pull/push/merge canonical).'
  }
)

$failures = @()
foreach ($spec in $specs) {
  if ($spec.Runner -like '*.ps1') {
    $runner = (Resolve-Path $spec.Runner).Path
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$runner`""
    $execute = $powerShell
  } else {
    $runner = (Resolve-Path $spec.Runner).Path
    $py = (Get-Command python.exe -ErrorAction Stop).Source
    $arguments = "`"$py`" `"$runner`" check"
    $execute = $powerShell
  }
  $existing = Get-ScheduledTask -TaskName $spec.Name -ErrorAction SilentlyContinue
  $matches = $false
  if ($existing) {
    $action = @($existing.Actions)[0]
    $matches = ($action.Execute -eq $execute -and $action.Arguments -eq $arguments)
  }

  if (-not $CheckOnly -and -not $matches) {
    # 不设置 WorkingDirectory：Task Scheduler 在本机(UnifiedSchedulingEngine)
    # 对 C:\Desktop\skills 的 cwd 解析会返回 ERROR_INVALID_NAME(0x8007010B)。
    # runners 一律用 $PSScriptRoot 自解析，不依赖 cwd。
    $taskAction = New-ScheduledTaskAction -Execute $execute -Argument $arguments
    Register-ScheduledTask -TaskName $spec.Name -Action $taskAction -Trigger $spec.Trigger `
      -Settings $settings -Description $spec.Description -Force | Out-Null
    $existing = Get-ScheduledTask -TaskName $spec.Name -ErrorAction Stop
    $action = @($existing.Actions)[0]
    $matches = ($action.Execute -eq $execute -and $action.Arguments -eq $arguments)
  }

  if ($matches) {
    Write-Host "TASK_OK: $($spec.Name) runner=$runner"
  } else {
    Write-Host "TASK_DRIFT: $($spec.Name) expected_runner=$runner"
    $failures += $spec.Name
  }
}

if ($failures.Count -gt 0) {
  Write-Host "GOVERNANCE_TASKS=DRIFT count=$($failures.Count)"
  exit 1
}
Write-Host "GOVERNANCE_TASKS=READY count=$($specs.Count)"
exit 0
