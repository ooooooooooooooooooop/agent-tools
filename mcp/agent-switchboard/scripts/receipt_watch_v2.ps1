# watcher v2: dual-channel supervision watcher
# Channel A: receipt file (terminal status + fresh updated_at)
# Channel B: supervisor state.json (anomaly: attention/failed/dead process)
# Either channel fires -> emit signal line and exit 0; window expiry -> window-elapsed-no-receipt
param(
  [Parameter(Mandatory=$true)][string]$ReceiptPath,
  [string]$SupervisorId = '',
  [int]$WindowMinutes = 30,
  [string]$AfterUpdatedAt = '',
  [string[]]$Terminal = @('ready_for_review','blocked','pushed'),
  [int]$IgnoreAttentionSeq = 0
)
$ErrorActionPreference = 'SilentlyContinue'
$deadline = (Get-Date).AddMinutes($WindowMinutes)
$statePath = ''
if ($SupervisorId -ne '') {
  $statePath = Join-Path $env:USERPROFILE ".agent-broker\supervisors\$SupervisorId\state.json"
}
$anomalyEvents = @('api_retry_exhausted','stall_timeout','tool_failure_threshold','turn_interrupted','autonomous_action_limit_reached')
while ((Get-Date) -lt $deadline) {
  # Channel A: receipt
  if (Test-Path $ReceiptPath) {
    try {
      $j = Get-Content $ReceiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
      $st = [string]$j.status
      $ua = [string]$j.updated_at
      if ($Terminal -contains $st) {
        if (($AfterUpdatedAt -eq '') -or ([string]::Compare($ua, $AfterUpdatedAt) -gt 0)) {
          Write-Output "RECEIPT-SIGNAL: status=$st updated_at=$ua"
          exit 0
        }
      }
    } catch {}
  }
  # Channel B: supervisor anomaly
  if ($statePath -ne '' -and (Test-Path $statePath)) {
    try {
      $s = Get-Content $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
      $sst = [string]$s.status
      if ($sst -eq 'failed' -or $sst -eq 'stopped') {
        Write-Output "SUPERVISOR-ANOMALY: status=$sst last_error=$($s.last_error)"
        exit 0
      }
      if ($null -ne $s.attention -and $anomalyEvents -contains [string]$s.attention.event_type -and [int]$s.attention.event_seq -gt $IgnoreAttentionSeq) {
        Write-Output "SUPERVISOR-ANOMALY: attention=$($s.attention.event_type) seq=$($s.attention.event_seq)"
        exit 0
      }
      if ($sst -ne 'stopped' -and $s.daemon_pid) {
        $proc = Get-Process -Id $s.daemon_pid
        if ($null -eq $proc) {
          Write-Output "SUPERVISOR-ANOMALY: daemon pid $($s.daemon_pid) dead while status=$sst"
          exit 0
        }
      }
    } catch {}
  }
  Start-Sleep -Seconds 60
}
Write-Output 'window-elapsed-no-receipt'
exit 0
