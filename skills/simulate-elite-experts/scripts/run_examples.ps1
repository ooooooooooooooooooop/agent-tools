param(
    [Parameter(Mandatory = $false)]
    [string]$Root = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Split-Path -Parent $PSScriptRoot
}

$examples = @(
    @{
        file = Join-Path $Root "examples\microservices-decision.md"
        profile = "classic"
    }
)

$results = @()
$failed = $false
$passedCount = 0
$failedCount = 0

foreach ($example in $examples) {
    $lintPath = Join-Path $Root "scripts\lint_response.ps1"
    $output = & $lintPath -FilePath $example.file -Profile $example.profile
    $parsed = $output | ConvertFrom-Json
    $results += $parsed
    if (-not $parsed.pass) {
        $failed = $true
        $failedCount += 1
    }
    else {
        $passedCount += 1
    }
}

$summary = [pscustomobject]@{
    root = $Root
    total = $results.Count
    passed = $passedCount
    failed = $failedCount
    results = $results
}

$summary | ConvertTo-Json -Depth 6

if ($failed) {
    exit 1
}

exit 0
