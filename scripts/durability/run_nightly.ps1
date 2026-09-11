# PersonalAI Durability Nightly — orchestrator (Windows Task Scheduler target).
# Schedule decides WHEN; run_backup.py decides WHAT with unified run identity,
# capture point, complete manifest, and isolated restore verification.
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$py = "python"

& $py "$repo\scripts\durability\run_backup.py"
$code = $LASTEXITCODE
exit $code
