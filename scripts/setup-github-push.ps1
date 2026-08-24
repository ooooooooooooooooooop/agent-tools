# setup-github-push.ps1 — 固定 GitHub 推送认证（绕过 cygwin 凭据助手）
#
# 背景：本机环境阻止 Git for Windows 的 cygwin shell（sh.exe/bash.exe）创建
# 信号管道，导致基于 shell 的 credential helper（gh auth git-credential）与
# askpass 提示脚本全部失效，`git push` 报 "could not read Username"。
#
# 本脚本把当前 gh 登录 token 以 basic-auth 形式写入仓库级
# http.https://github.com/.extraheader，Git 推送时直接携带该请求头完成认证，
# 完全不经过凭据助手/提示脚本，`git push` 即可直接使用。
#
# 用法：  powershell -ExecutionPolicy Bypass -File scripts/setup-github-push.ps1
# 刷新：  gh token 失效/刷新后重跑本脚本（幂等，覆盖旧值）。
# 撤销：  git config --local --unset http.https://github.com/.extraheader
param(
    [string]$Remote = "origin"
)

$ErrorActionPreference = "Stop"

$repoRoot = git rev-parse --show-toplevel 2>$null
if (-not $repoRoot) {
    Write-Error "Not a git repository; run this script from the repo root."
    exit 1
}

$url = git remote get-url $Remote
if ($url -notmatch "github\.com") {
    Write-Warning "Remote '$Remote' is not a github.com URL ($url); nothing to configure."
    exit 0
}

$token = gh auth token 2>$null
if (-not $token) {
    Write-Error "gh is not authenticated. Run: gh auth login"
    exit 1
}

$basic = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("oauth2:$token"))
git config --local "http.https://github.com/.extraheader" "AUTHORIZATION: basic $basic"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to write git config."
    exit 1
}

Write-Host "OK: github.com basic-auth extra header pinned for remote '$Remote'."
Write-Host "Now 'git push' works directly (no credential helper needed)."
Write-Host "Re-run this script if the gh token is refreshed or revoked."
