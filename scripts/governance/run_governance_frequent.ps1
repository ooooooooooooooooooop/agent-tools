# PersonalAI Governance — FREQUENT (daily, cheap): drift/capability/static/secret/rpo.
# Child checks keep their domain/finding exit codes; the adapter translates only
# the scheduler boundary and persists the two-dimensional result.
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = (Get-Command python.exe -ErrorAction Stop).Source
& $python "$repo\scripts\governance\runner_adapter.py" frequent
exit $LASTEXITCODE
