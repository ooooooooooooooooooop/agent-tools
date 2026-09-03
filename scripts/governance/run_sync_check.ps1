# PersonalAI Sync Check — scheduler adapter for check-only classification.
# personal_ai_sync.py keeps its direct PASS/REVIEW/BLOCKED CLI contract.  This
# adapter makes a completed REVIEW observable as scheduler execution success.
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = (Get-Command python.exe -ErrorAction Stop).Source
& $python "$repo\scripts\governance\runner_adapter.py" sync-check
exit $LASTEXITCODE
