# Verify guard-convergence.ps1 version-exit branches with synthetic checkouts.
# No side effects: REVIEW and NOT_REQUIRED branches only read the target package;
# APPLY is exercised against the real environment separately.
param()
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$guard = Join-Path $here "guard-convergence.ps1"
$tmp = Join-Path $env:TEMP ("dsh-guard-branch-" + [guid]::NewGuid().ToString("N"))
function New-FakeCheckout($version, $withFixes) {
  $root = Join-Path $tmp ("checkout-" + $version.Replace('.', '_') + "-" + $(if ($withFixes) { "fixed" } else { "plain" }) + "-" + (Split-Path $PSScriptRoot -Leaf))
  return $root
}
function Make-Fake($ver, $withFixes) {
  $root = Join-Path $tmp ("co-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
  $scoped = Join-Path $root "node_modules\@deepseek-ai"
  New-Item -ItemType Directory -Force -Path (Join-Path $scoped "dsh") | Out-Null
  New-Item -ItemType Directory -Force -Path (Join-Path $scoped "dsh-compaction-basic\lib") | Out-Null
  '{"name":"@deepseek-ai/dsh","version":"0.0.0"}' | Set-Content -Path (Join-Path $scoped "dsh\package.json") -Encoding UTF8
  (@{ name = "@deepseek-ai/dsh-compaction-basic"; version = $ver } | ConvertTo-Json) | Set-Content -Path (Join-Path $scoped "dsh-compaction-basic\package.json") -Encoding UTF8
  $lib = @"
export const marker = 'fake';
"@
  if ($withFixes) {
    $lib = @"
function isCompactCheckpointSource(s){return s}
let failedPressureRegion = new WeakMap();
const summaryBase = 'summary is not smaller than the shadowed content';
export { isCompactCheckpointSource, failedPressureRegion, summaryBase };
"@
  }
  [System.IO.File]::WriteAllText((Join-Path $scoped "dsh-compaction-basic\lib\index.js"), $lib)
  return $root
}
try {
  New-Item -ItemType Directory -Force -Path $tmp | Out-Null
  # REVIEW branch: version changed, fixes absent -> exit 2 + REVIEW
  $r1 = Make-Fake "9.9.9" $false
  powershell -ExecutionPolicy Bypass -File $guard -Checkout $r1 *> "$tmp\out-review.txt"
  $code1 = $LASTEXITCODE
  $hasReview = (Get-Content "$tmp\out-review.txt" -Raw) -match "REVIEW"
  "REVIEW branch: exit=$code1 text=$hasReview"
  if ($code1 -ne 2 -or -not $hasReview) { throw "REVIEW branch failed" }
  # NOT_REQUIRED branch: version changed, fixes present -> exit 0 + NOT_REQUIRED
  $r2 = Make-Fake "9.9.9" $true
  powershell -ExecutionPolicy Bypass -File $guard -Checkout $r2 *> "$tmp\out-notreq.txt"
  $code2 = $LASTEXITCODE
  $hasNotReq = (Get-Content "$tmp\out-notreq.txt" -Raw) -match "NOT_REQUIRED"
  "NOT_REQUIRED branch: exit=$code2 text=$hasNotReq"
  if ($code2 -ne 0 -or -not $hasNotReq) { throw "NOT_REQUIRED branch failed" }
  Write-Host "GUARD BRANCH VERIFY: PASS (REVIEW exit=2, NOT_REQUIRED exit=0)"
} finally {
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}