param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,

    [Parameter(Mandatory = $false)]
    [ValidateSet("micro", "lean", "classic", "deep")]
    [string]$Profile = "classic"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $FilePath)) {
    Write-Error "File not found: $FilePath"
}

$text = Get-Content -Raw -Encoding UTF8 -LiteralPath $FilePath
$text = $text -replace "`r`n", "`n"

$errors = New-Object System.Collections.Generic.List[string]

# Profile-dependent heading expectations
$profileConfig = @{
    "micro" = @{
        expectedCount = 5
        patterns = @(
            '(?m)^##\s*1\.\s*Good Group To Explore X\b',
            '(?m)^##\s*2\.\s*Dialogue Round 1: Initial Positions\b',
            '(?m)^##\s*3\.\s*Dialogue Round 2: Final Statements\b',
            '(?m)^##\s*4\.\s*Moderator Synthesis\b',
            '(?m)^##\s*5\.\s*Uncertainty Ledger\b'
        )
        minRosterBullets = 2
        roundHeaders = @(
            '^##\s*2\.\s*Dialogue Round 1: Initial Positions\b.*$',
            '^##\s*3\.\s*Dialogue Round 2: Final Statements\b.*$'
        )
        minTurnsPerRound = 2
    }
    "lean" = @{
        expectedCount = 7
        patterns = @(
            '(?m)^##\s*1\.\s*Good Group To Explore X\b',
            '(?m)^##\s*2\.\s*Dialogue Round 1: Initial Positions\b',
            '(?m)^##\s*3\.\s*Dialogue Round 2: Cross-Examination\b',
            '(?m)^##\s*4\.\s*Dialogue Round 3: Revised Positions\b',
            '(?m)^##\s*5\.\s*Dialogue Round 4: Final Statements\b',
            '(?m)^##\s*6\.\s*Moderator Synthesis\b',
            '(?m)^##\s*7\.\s*Uncertainty Ledger\b'
        )
        minRosterBullets = 4
        roundHeaders = @(
            '^##\s*2\.\s*Dialogue Round 1: Initial Positions\b.*$',
            '^##\s*3\.\s*Dialogue Round 2: Cross-Examination\b.*$',
            '^##\s*4\.\s*Dialogue Round 3: Revised Positions\b.*$',
            '^##\s*5\.\s*Dialogue Round 4: Final Statements\b.*$'
        )
        minTurnsPerRound = 4
    }
    "classic" = @{
        expectedCount = 7
        patterns = @(
            '(?m)^##\s*1\.\s*Good Group To Explore X\b',
            '(?m)^##\s*2\.\s*Dialogue Round 1: Initial Positions\b',
            '(?m)^##\s*3\.\s*Dialogue Round 2: Cross-Examination\b',
            '(?m)^##\s*4\.\s*Dialogue Round 3: Revised Positions\b',
            '(?m)^##\s*5\.\s*Dialogue Round 4: Final Statements\b',
            '(?m)^##\s*6\.\s*Moderator Synthesis\b',
            '(?m)^##\s*7\.\s*Uncertainty Ledger\b'
        )
        minRosterBullets = 4
        roundHeaders = @(
            '^##\s*2\.\s*Dialogue Round 1: Initial Positions\b.*$',
            '^##\s*3\.\s*Dialogue Round 2: Cross-Examination\b.*$',
            '^##\s*4\.\s*Dialogue Round 3: Revised Positions\b.*$',
            '^##\s*5\.\s*Dialogue Round 4: Final Statements\b.*$'
        )
        minTurnsPerRound = 4
    }
    "deep" = @{
        expectedCount = 9
        patterns = @(
            '(?m)^##\s*1\.\s*Good Group To Explore X\b',
            '(?m)^##\s*2\.\s*Dialogue Round 1: Initial Positions\b',
            '(?m)^##\s*3\.\s*Dialogue Round 2: Cross-Examination\b',
            '(?m)^##\s*4\.\s*Dialogue Round 3: Revised Positions\b',
            '(?m)^##\s*5\.\s*Dialogue Round 4: Final Statements\b',
            '(?m)^##\s*6\.\s*Dialogue Round 5: Stress Test\b',
            '(?m)^##\s*7\.\s*Dialogue Round 6: Contingency Planning\b',
            '(?m)^##\s*8\.\s*Moderator Synthesis\b',
            '(?m)^##\s*9\.\s*Uncertainty Ledger\b'
        )
        minRosterBullets = 4
        roundHeaders = @(
            '^##\s*2\.\s*Dialogue Round 1: Initial Positions\b.*$',
            '^##\s*3\.\s*Dialogue Round 2: Cross-Examination\b.*$',
            '^##\s*4\.\s*Dialogue Round 3: Revised Positions\b.*$',
            '^##\s*5\.\s*Dialogue Round 4: Final Statements\b.*$',
            '^##\s*6\.\s*Dialogue Round 5: Stress Test\b.*$',
            '^##\s*7\.\s*Dialogue Round 6: Contingency Planning\b.*$'
        )
        minTurnsPerRound = 4
    }
}

$config = $profileConfig[$Profile]

# Check heading count
$headingMatches = [regex]::Matches($text, '(?m)^##\s*\d+\..+$')
if ($headingMatches.Count -ne $config.expectedCount) {
    $errors.Add("Profile '$Profile' expects exactly $($config.expectedCount) numbered top-level headings, found $($headingMatches.Count).")
}

# Check required headings in order
$lastIndex = -1
for ($i = 0; $i -lt $config.patterns.Count; $i++) {
    $pattern = $config.patterns[$i]
    $match = [regex]::Match($text, $pattern)
    if (-not $match.Success) {
        $errors.Add("Missing required section #$($i + 1) for profile '$Profile'.")
        continue
    }
    if ($match.Index -lt $lastIndex) {
        $errors.Add("Section order is incorrect around section #$($i + 1).")
    }
    $lastIndex = $match.Index
}

# Helper function
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

# Check roster bullets
$section1 = Get-SectionBlock -InputText $text -HeaderRegex '^##\s*1\.\s*Good Group To Explore X\b.*$'
$rosterBulletCount = ([regex]::Matches($section1, '(?m)^-\s+')).Count
if ($rosterBulletCount -lt $config.minRosterBullets) {
    $errors.Add("Section 1 has fewer than $($config.minRosterBullets) roster bullets ($rosterBulletCount).")
}

$requiredSection1Patterns = @(
    '(?i)(Decision frame|\u51B3\u7B56\u6846\u67B6)',
    '(?i)(Execution mode|\u6267\u884C\u6A21\u5F0F)',
    '(?i)(Context basis|\u4E0A\u4E0B\u6587\u4F9D\u636E)',
    '(?i)(Roster score|\u4EBA\u9009\u8BC4\u5206)',
    '(?i)(Roster diversity|\u4EBA\u7FA4\u591A\u6837\u6027)'
)

foreach ($pattern in $requiredSection1Patterns) {
    if ($section1 -notmatch $pattern) {
        $errors.Add("Section 1 is missing required field matching pattern: $pattern")
    }
}

if ($section1 -notmatch '(?i)(Real Person A|\u73B0\u5B9E\u4EBA\u7269A)') {
    $errors.Add("Section 1 is missing Real Person A.")
}
if ($section1 -notmatch '(?i)(Domain Expert Archetype|\u9886\u57DF\u4E13\u5BB6\u62BD\u8C61)') {
    $errors.Add("Section 1 is missing Domain Expert Archetype.")
}
if ($Profile -ne "micro") {
    if ($section1 -notmatch '(?i)(Real Person B|\u73B0\u5B9E\u4EBA\u7269B)') {
        $errors.Add("Section 1 is missing Real Person B.")
    }
    if ($section1 -notmatch '(?i)(Omniscient Agent Archetype|\u5168\u77E5\u667A\u80FD\u4F53\u62BD\u8C61)') {
        $errors.Add("Section 1 is missing Omniscient Agent Archetype.")
    }
}

# Check turns per round
for ($r = 0; $r -lt $config.roundHeaders.Count; $r++) {
    $block = Get-SectionBlock -InputText $text -HeaderRegex $config.roundHeaders[$r]
    $turnCount = ([regex]::Matches($block, '(?m)^-\s*`?\[')).Count
    if ($turnCount -ne $config.minTurnsPerRound) {
        $errors.Add("Round $($r + 1) expects exactly $($config.minTurnsPerRound) role turns, found $turnCount.")
    }

    $roundConfidenceTagCount = ([regex]::Matches($block, '(?i)\[confidence:\s*(high|medium|low)\]|\[\u7F6E\u4FE1\u5EA6:\s*(\u9AD8|\u4E2D|\u4F4E)\]')).Count
    $minRoundConfidence = if ($Profile -eq "micro") { 1 } else { 2 }
    if ($roundConfidenceTagCount -lt $minRoundConfidence) {
        $errors.Add("Round $($r + 1) expected at least $minRoundConfidence real-person confidence tags, found $roundConfidenceTagCount.")
    }
}

# Check simulation boundary marker
if ($text -notmatch '(?i)(simulated viewpoints|public work|\u516C\u5F00\u4FE1\u606F|\u6A21\u62DF\u63A8\u65AD)') {
    $errors.Add("Missing explicit simulation boundary marker.")
}

# Check confidence tags for real person turns per round
# micro = 1 real person per round, others = 2 real persons per round
$minConfidencePerRound = if ($Profile -eq "micro") { 1 } else { 2 }
$totalExpectedConfidence = $minConfidencePerRound * $config.roundHeaders.Count

$confidenceTagCount = ([regex]::Matches($text, '(?i)\[confidence:\s*(high|medium|low)\]|\[\u7F6E\u4FE1\u5EA6:\s*(\u9AD8|\u4E2D|\u4F4E)\]')).Count
if ($confidenceTagCount -lt $totalExpectedConfidence) {
    $errors.Add("Expected at least $totalExpectedConfidence confidence tags ($minConfidencePerRound per round x $($config.roundHeaders.Count) rounds), found $confidenceTagCount.")
}

# Check Post-Use Self-Check appendix
if ($text -notmatch '(?i)(Post-Use Self-Check|\u4F7F\u7528\u540E\u81EA\u68C0\u6E05\u5355)') {
    $errors.Add("Missing mandatory Post-Use Self-Check appendix.")
}
else {
    $selfCheckHeading = [regex]::Match($text, '(?im)^#{2,3}\s*(Post-Use Self-Check|\u4F7F\u7528\u540E\u81EA\u68C0\u6E05\u5355).*$')
    if ($selfCheckHeading.Success) {
        $selfCheckText = $text.Substring($selfCheckHeading.Index + $selfCheckHeading.Length)
        $selfCheckQuestions = ([regex]::Matches($selfCheckText, '(?m)^\d+\.\s+')).Count
        if ($selfCheckQuestions -ne 5) {
            $errors.Add("Post-Use Self-Check expects exactly 5 numbered questions, found $selfCheckQuestions.")
        }
    }
}

# Check uncertainty snapshots in dialogue rounds (at least one per round for rounds that have them)
$uncertaintySnapshotCount = ([regex]::Matches($text, '(?im)^-\s*`?(Uncertainty snapshot|\u4E0D\u786E\u5B9A\u6027\u5FEB\u7167)')).Count
# Check uncertainty snapshots in dialogue rounds
# Rolling tracker covers Rounds 1-3 for classic/lean, Round 1 for micro, Rounds 1-3 for deep (R4-6 are action-oriented)
$expectedSnapshots = switch ($Profile) {
    "micro"   { 1 }
    "lean"    { 3 }
    "classic" { 3 }
    "deep"    { 3 }
}
if ($uncertaintySnapshotCount -lt $expectedSnapshots) {
    $errors.Add("Expected at least $expectedSnapshots uncertainty snapshots across dialogue rounds, found $uncertaintySnapshotCount.")
}

$result = [pscustomobject]@{
    file = $FilePath
    profile = $Profile
    pass = ($errors.Count -eq 0)
    hard_gate_errors = @($errors)
}

$result | ConvertTo-Json -Depth 4

if ($errors.Count -gt 0) {
    exit 1
}

exit 0
