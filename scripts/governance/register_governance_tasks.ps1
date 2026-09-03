param(
  [switch]$CheckOnly
)

# Idempotently register the existing Personal AI governance/durability/sync-check
# runners with the Windows Task Scheduler. This is deployment/recovery glue, not a
# new scheduler. Canonical contracts: run_governance_*.ps1 (governance),
# run_nightly.ps1 (durability), run_sync_check.ps1 (sync check-only adapter).
#
# Production-ownership guard (2026-09-03 remediation):
#   TEST/DRILL MAY OBSERVE PRODUCTION BUT MUST NOT BECOME PRODUCTION OWNER.
#   A bootstrap drill once registered these tasks with actions pointing into a
#   %TEMP%\bootstrap-drill-* mirror; when Temp was cleaned the nightly backup
#   silently failed for days (0xFFFD0000). Prevention is mechanical, not advisory:
#     1. The resolved repo root must NOT be an ephemeral path (%TEMP%,
#        AppData\Local\Temp, bootstrap-drill-*, .claude\worktrees, temp fixtures).
#        -> REGISTRATION_REJECTED_EPHEMERAL_PATH
#     2. The repo root must be anchored in machine-local durable config
#        (personal-ai-state/sync/this-device.yaml repos list).
#        -> REGISTRATION_REJECTED_NON_CANONICAL
#     3. Every registered action target must exist at registration time.
$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path

function Test-EphemeralPath {
  param([Parameter(Mandatory = $true)][string]$PathToTest)
  $n = $PathToTest.ToLowerInvariant().TrimEnd('\')
  $tempCandidates = @($env:TEMP, $env:TMP, (Join-Path $env:LOCALAPPDATA 'Temp'), 'C:\Windows\Temp') |
    Where-Object { -not [string]::IsNullOrEmpty($_) }
  foreach ($t in $tempCandidates) {
    $tn = $t.ToLowerInvariant().TrimEnd('\')
    if ($n.StartsWith($tn)) { return $true }
  }
  $ephemeralMarkers = @('bootstrap-drill-', '\.claude\worktrees', '\appdata\local\temp\', '\windows\temp\')
  foreach ($m in $ephemeralMarkers) {
    if ($n.Contains($m)) { return $true }
  }
  return $false
}

if (Test-EphemeralPath -PathToTest $repo) {
  throw "REGISTRATION_REJECTED_EPHEMERAL_PATH: $repo (drill/test mirrors must never own production scheduled tasks)"
}

# Canonical anchor: this checkout must be listed in the machine-local durable
# device config. No hardcoded user directories — the machine config is the SSOT.
$stateRoot = $env:PERSONAL_AI_STATE
if (-not $stateRoot) { $stateRoot = Join-Path $HOME 'personal-ai-state' }
$deviceCfgPath = Join-Path $stateRoot 'sync\this-device.yaml'
if (-not (Test-Path -LiteralPath $deviceCfgPath)) {
  throw "REGISTRATION_REJECTED_NO_DEVICE_CONFIG: $deviceCfgPath (cannot prove canonical checkout)"
}
$cfgText = Get-Content -LiteralPath $deviceCfgPath -Raw
$repoAnchored = $false
foreach ($line in ($cfgText -split "`r?`n")) {
  if ($line -match '^\s*-\s*(.+?)\s*$') {
    $entry = $Matches[1].Trim().Trim('"').Trim("'")
    try {
      $entryResolved = (Resolve-Path -LiteralPath $entry -ErrorAction Stop).Path.TrimEnd('\')
      if ([StringComparer]::OrdinalIgnoreCase.Equals($entryResolved, $repo.TrimEnd('\'))) { $repoAnchored = $true; break }
    } catch { }
  }
}
if (-not $repoAnchored) {
  throw "REGISTRATION_REJECTED_NON_CANONICAL: $repo is not listed in $deviceCfgPath repos (machine-local drift? fix the config, do not bypass)"
}

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
  $runner = (Resolve-Path -LiteralPath $spec.Runner -ErrorAction Stop).Path
  if (Test-EphemeralPath -PathToTest $runner) {
    throw "REGISTRATION_REJECTED_EPHEMERAL_PATH: runner $runner"
  }
  if (-not (Test-Path -LiteralPath $runner)) {
    throw "REGISTRATION_REJECTED_MISSING_RUNNER: $runner"
  }
  $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$runner`""
  $execute = $powerShell
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
