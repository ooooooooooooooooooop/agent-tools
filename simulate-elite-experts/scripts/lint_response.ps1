param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $FilePath)) {
    Write-Error "File not found: $FilePath"
}

$text = Get-Content -Raw -Encoding UTF8 -LiteralPath $FilePath
$text = $text -replace "`r`n", "`n"

$errors = New-Object System.Collections.Generic.List[string]

$requiredHeadingPatterns = @(
    '(?m)^##\s*1\.\s*Good Group To Explore X\b',
    '(?m)^##\s*2\.\s*Dialogue Round 1: Initial Positions\b',
    '(?m)^##\s*3\.\s*Dialogue Round 2: Cross-Examination\b',
    '(?m)^##\s*4\.\s*Dialogue Round 3: Revised Positions\b',
    '(?m)^##\s*5\.\s*Dialogue Round 4: Final Statements\b',
    '(?m)^##\s*6\.\s*Moderator Synthesis\b',
    '(?m)^##\s*7\.\s*Uncertainty Ledger\b'
)

$headingMatches = [regex]::Matches($text, '(?m)^##\s*\d+\..+$')
if ($headingMatches.Count -ne 7) {
    $errors.Add("Expected 7 top-level required headings, found $($headingMatches.Count).")
}

$lastIndex = -1
for ($i = 0; $i -lt $requiredHeadingPatterns.Count; $i++) {
    $pattern = $requiredHeadingPatterns[$i]
    $match = [regex]::Match($text, $pattern)
    if (-not $match.Success) {
        $errors.Add("Missing required section #$($i + 1).")
        continue
    }
    if ($match.Index -lt $lastIndex) {
        $errors.Add("Section order is incorrect around section #$($i + 1).")
    }
    $lastIndex = $match.Index
}

function Get-SectionBlock {
    param(
        [string]$InputText,
        [string]$HeaderRegex
    )

    $start = [regex]::Match($InputText, $HeaderRegex, [System.Text.RegularExpressions.RegexOptions]::Multiline)
    if (-not $start.Success) { return "" }
    $tail = $InputText.Substring($start.Index + $start.Length)
    $next = [regex]::Match($tail, '(?m)^##\s*\d+\..+')
    if ($next.Success) {
        return $tail.Substring(0, $next.Index)
    }
    return $tail
}

$section1 = Get-SectionBlock -InputText $text -HeaderRegex '^##\s*1\.\s*Good Group To Explore X\b.*$'
$rosterBulletCount = ([regex]::Matches($section1, '(?m)^-\s+')).Count
if ($rosterBulletCount -lt 4) {
    $errors.Add("Section 1 has fewer than 4 roster bullets ($rosterBulletCount).")
}

$roundHeaders = @(
    '^##\s*2\.\s*Dialogue Round 1: Initial Positions\b.*$',
    '^##\s*3\.\s*Dialogue Round 2: Cross-Examination\b.*$',
    '^##\s*4\.\s*Dialogue Round 3: Revised Positions\b.*$',
    '^##\s*5\.\s*Dialogue Round 4: Final Statements\b.*$'
)

for ($r = 0; $r -lt $roundHeaders.Count; $r++) {
    $block = Get-SectionBlock -InputText $text -HeaderRegex $roundHeaders[$r]
    $turnCount = ([regex]::Matches($block, '(?m)^-\s*\[')).Count
    if ($turnCount -lt 4) {
        $errors.Add("Round $($r + 1) has fewer than 4 role turns ($turnCount).")
    }
}

if ($text -notmatch '(?i)(simulated viewpoints|public work|\u516C\u5F00\u4FE1\u606F|\u6A21\u62DF\u63A8\u65AD)') {
    $errors.Add("Missing explicit simulation boundary marker.")
}

$result = [pscustomobject]@{
    file = $FilePath
    pass = ($errors.Count -eq 0)
    hard_gate_errors = @($errors)
}

$result | ConvertTo-Json -Depth 4

if ($errors.Count -gt 0) {
    exit 1
}

exit 0
