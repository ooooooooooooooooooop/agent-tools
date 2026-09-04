[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$DshUrl = 'http://127.0.0.1:3080/'
$LogPath = Join-Path $env:TEMP 'dsh-web-shortcut.log'
$DshHome = if ([string]::IsNullOrWhiteSpace($env:DSH_HOME)) {
    Join-Path $env:USERPROFILE '.dsh'
} else {
    $env:DSH_HOME
}
$LauncherPath = Join-Path $DshHome 'profiles\web\dsh-launch-web.ps1'

function Write-Log {
    param([string]$Message)

    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'), $Message
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function Test-DshWebCommandLine {
    param([AllowNull()][string]$CommandLine)

    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return $false
    }

    return ($CommandLine -match '(?i)@deepseek-ai[\\/]+dsh' -and $CommandLine -match '(?i)\bweb\b')
}

function Get-ProcessSnapshot {
    @(Get-CimInstance -ClassName Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $null -ne $_.ProcessId })
}

function Get-DshWebProcesses {
    param([object[]]$Snapshot)

    @($Snapshot | Where-Object {
        Test-DshWebCommandLine -CommandLine ([string]$_.CommandLine)
    })
}

function Get-ProcessTreeIds {
    param(
        [object[]]$Snapshot,
        [int[]]$RootIds
    )

    $ids = New-Object 'System.Collections.Generic.HashSet[int]'
    foreach ($rootId in $RootIds) {
        [void]$ids.Add($rootId)
    }

    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($item in $Snapshot) {
            $processId = [int]$item.ProcessId
            $parentProcessId = [int]$item.ParentProcessId
            if ($ids.Contains($parentProcessId) -and $ids.Add($processId)) {
                $changed = $true
            }
        }
    }

    @($ids | ForEach-Object { [int]$_ })
}

function Stop-DshWeb {
    param([object[]]$Snapshot)

    $dshProcesses = @(Get-DshWebProcesses -Snapshot $Snapshot)
    if ($dshProcesses.Count -eq 0) {
        Write-Log 'No existing DSH Web process matched the managed launcher command line.'
        return
    }

    $rootIds = @($dshProcesses | ForEach-Object { [int]$_.ProcessId })
    $treeIds = @(Get-ProcessTreeIds -Snapshot $Snapshot -RootIds $rootIds)
    Write-Log ('Stopping DSH Web process tree: ' + (($treeIds | Sort-Object) -join ', '))

    foreach ($processId in ($treeIds | Sort-Object -Descending)) {
        try {
            Stop-Process -Id $processId -Force -ErrorAction Stop
        } catch {
            Write-Log ("Process {0} was already stopped or could not be stopped: {1}" -f $processId, $_.Exception.Message)
        }
    }

    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        $remaining = @(Get-DshWebProcesses -Snapshot (Get-ProcessSnapshot))
        if ($remaining.Count -eq 0) {
            break
        }
        Start-Sleep -Milliseconds 250
    }

    $remaining = @(Get-DshWebProcesses -Snapshot (Get-ProcessSnapshot))
    if ($remaining.Count -gt 0) {
        throw 'Unable to stop the existing DSH Web process tree.'
    }
}

function Test-DshReady {
    try {
        $dshProcesses = @(Get-DshWebProcesses -Snapshot (Get-ProcessSnapshot))
        if ($dshProcesses.Count -eq 0) {
            return $false
        }

        $response = Invoke-WebRequest -UseBasicParsing -Uri $DshUrl -TimeoutSec 1
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
    } catch {
        return $false
    }
}

function Show-ErrorBalloon {
    param([string]$Text)
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
        $notify = New-Object System.Windows.Forms.NotifyIcon
        $notify.Icon = [System.Drawing.SystemIcons]::Error
        $notify.Visible = $true
        $notify.ShowBalloonTip(10000, 'DSH Web', $Text, [System.Windows.Forms.ToolTipIcon]::Error)
        Start-Sleep -Seconds 6
        $notify.Dispose()
    } catch {}
}

function Wait-PortFree {
    param([int]$Port, [int]$TimeoutMs = 15000, [switch]$FailClosed)
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt $TimeoutMs) {
        $inUse = netstat -ano 2>$null | Select-String ":$Port\s" | Select-String 'LISTENING|ESTABLISHED|TIME_WAIT'
        if (-not $inUse -or $inUse.Count -eq 0) {
            Write-Log "Port $Port is free."
            return
        }
        Start-Sleep -Milliseconds 500
    }
    if ($FailClosed) {
        # Never start a second host over a possibly-live one. Residual
        # connections after the timeout mean the old owner may still be bound
        # to the port (or to the shared workspace registry); starting anyway is
        # the two-writer race that produced the workspace.json lost-update
        # incident (DSH_WORKSPACE_REGISTRY_INTEGRITY, 2026-09-04).
        throw "Port $Port still has residual connections after ${TimeoutMs}ms; refusing to start a second DSH Web host (RESTART_BLOCKED_OLD_HOST_NOT_TERMINATED)."
    }
    Write-Log "Warning: port $Port still has residual connections after ${TimeoutMs}ms, proceeding anyway."
}

try {
    Write-Log 'Starting silent DSH Web restart.'

    if (-not (Test-Path -LiteralPath $LauncherPath)) {
        throw "Managed DSH launcher not found: $LauncherPath"
    }

    # Single-instance guard: if a DSH Web host is already running AND healthy,
    # reuse it instead of kill-and-restarting. A second click on the shortcut
    # (or a sync-engine restart racing a manual one) must never spawn a second
    # host over the same workspace registry.
    if (Test-DshReady) {
        Write-Log 'DSH Web is already running and healthy; reusing the existing host (no restart).'
        Start-Process -FilePath 'explorer.exe' -ArgumentList $DshUrl | Out-Null
        exit 0
    }

    Stop-DshWeb -Snapshot (Get-ProcessSnapshot)

    # Wait for port 3080 to be fully released before starting the new process.
    # Fail closed: residual connections mean the old owner may still hold the
    # port / registry — never start a second host over it.
    Wait-PortFree -Port 3080 -FailClosed

    # Use WMI Win32_Process.Create to launch a fully detached process that is
    # not part of this process's Windows Job Object. This prevents Windows from
    # killing the DSH Web node process when the shortcut's shell process exits.
    $launcherDir = Split-Path -Parent $LauncherPath
    $cmdLine = "powershell.exe -NoProfile -WindowStyle Hidden -File `"$LauncherPath`""
    $startInfo = ([wmiclass]"Win32_ProcessStartup").CreateInstance()
    $startInfo.ShowWindow = 0  # SW_HIDE
    $result = ([wmiclass]"Win32_Process").Create($cmdLine, $launcherDir, $startInfo)
    if ($result.ReturnValue -ne 0) {
        throw "WMI Win32_Process.Create failed with return value $($result.ReturnValue)"
    }
    $launchPid = $result.ProcessId
    Write-Log ("Started managed DSH Web process with launcher PID {0} (detached via WMI)." -f $launchPid)

    $ready = $false
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        if (Test-DshReady) {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }

    if (-not $ready) {
        throw "DSH Web did not become ready at $DshUrl within 45s. See $LogPath"
    }

    Write-Log 'DSH Web is ready; opening the local page.'
    Start-Process -FilePath 'explorer.exe' -ArgumentList $DshUrl | Out-Null
    Write-Log 'Silent DSH Web restart completed.'
    exit 0
} catch {
    Write-Log ("ERROR: {0}" -f $_.Exception.Message)
    Show-ErrorBalloon $_.Exception.Message
    exit 1
}
