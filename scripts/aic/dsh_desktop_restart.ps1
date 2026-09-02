[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$DshUrl = 'http://127.0.0.1:3080/'
$WorkingDirectory = [Environment]::GetFolderPath('UserProfile')
$LogPath = Join-Path $env:TEMP 'dsh-web-shortcut.log'
$NpxPath = Join-Path ${env:ProgramFiles} 'nodejs\npx.cmd'

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
        Write-Log 'No existing DSH Web process matched the npx command line.'
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

try {
    Write-Log 'Starting silent DSH Web restart.'

    if (-not (Test-Path -LiteralPath $NpxPath)) {
        $npxCommand = Get-Command npx.cmd -ErrorAction Stop
        $NpxPath = $npxCommand.Path
    }

    Stop-DshWeb -Snapshot (Get-ProcessSnapshot)

    $launchProcess = Start-Process -FilePath $NpxPath `
        -ArgumentList @('@deepseek-ai/dsh', 'web', '--no-open') `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden `
        -PassThru
    Write-Log ("Started npx DSH Web process with launcher PID {0}." -f $launchProcess.Id)

    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if (Test-DshReady) {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }

    if (-not $ready) {
        throw "DSH Web did not become ready at $DshUrl. See $LogPath"
    }

    Write-Log 'DSH Web is ready; opening the local page.'
    Start-Process -FilePath 'explorer.exe' -ArgumentList $DshUrl | Out-Null
    Write-Log 'Silent DSH Web restart completed.'
    exit 0
} catch {
    Write-Log ("ERROR: {0}" -f $_.Exception.Message)
    exit 1
}
