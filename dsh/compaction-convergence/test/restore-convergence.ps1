# Restore the pristine configuration: remove the profile-local overlay fork, the
# patch overlay row, and the dependency junction created by install-convergence.ps1.
# Idempotent; no-op when overlay is not installed.
param(
  [string]$Checkout = ""
)
$ErrorActionPreference = "Stop"
$profileRoot = Join-Path $env:USERPROFILE ".dsh\profiles\web"
$forkDest = Join-Path $profileRoot "plugins\dsh-compaction-convergence"
$patch = Join-Path $profileRoot "cordis.patch.yml"
$bak = Join-Path $profileRoot "cordis.patch.yml.dsh-conv.bak"

# 1) fork directory
if (Test-Path $forkDest) {
  Remove-Item -Recurse -Force $forkDest
  Write-Host "removed profile fork $forkDest"
} else {
  Write-Host "no profile fork present"
}

# 2) patch overlay row — strip the overlay block. The .dsh-conv.bak snapshot is kept
#    as an extra safety copy but is NOT blindly restored: it predates manual fixes.
$content = Get-Content $patch -Raw -Encoding UTF8
$content = [regex]::Replace(
  $content,
  '(?ms)# DSH compaction-convergence overlay.*?maxOverflowRetries: 2\r?\n',
  ''
)
[System.IO.File]::WriteAllText($patch, $content)
Write-Host "stripped overlay block from cordis.patch.yml"

# 3) dependency junction (only remove if it points at the checkout scoped dir)
$link = Join-Path $profileRoot "node_modules\@deepseek-ai"
if (Test-Path $link) {
  $item = Get-Item $link -Force
  if ($item.LinkType -eq "Junction") { Remove-Item -Force $link; Write-Host "removed dependency junction" }
  else { Write-Host "left @deepseek-ai directory untouched (not a junction)" }
}

Write-Host "NOTE: restart the DSH GUI process to reload the upstream module."