# PersonalAI Governance — WEEKLY (less frequent): upstream discovery/review,
# model state/health, dead config, stale memory, duplicate rules, project state.
# Expensive paid-model canary stays manual. Discovery/proposals never auto-adopt.
# Child checks keep their domain/finding exit codes; the adapter translates only
# the scheduler boundary and persists the two-dimensional result.
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = (Get-Command python.exe -ErrorAction Stop).Source
& $python "$repo\scripts\governance\runner_adapter.py" weekly
exit $LASTEXITCODE
