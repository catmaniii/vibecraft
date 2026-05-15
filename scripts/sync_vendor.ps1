# sync_vendor.ps1 — 从 upstream 重新拉 vendor 目录
#
# 用法（在仓库根运行）：
#   .\scripts\sync_vendor.ps1 -Repo sharpy
#
# 支持的 Repo 参数与对应 GitHub URL：
#   sharpy -> https://github.com/DrInfy/sharpy-sc2
#
# 流程：
#   1. 在临时目录 clone --depth 1
#   2. 记录 commit hash
#   3. 删目标 vendor 目录
#   4. 拷贝 clone 到 vendor/<name>
#   5. 删 .git（避免 nested git）
#   6. 保留 LICENSE（原 LICENSE 若存在则被新版覆盖）
#   7. 更新 ATTRIBUTION.md 里的 commit hash + date

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("sharpy")]
    [string]$Repo
)

$repoMap = @{
    "sharpy" = "https://github.com/DrInfy/sharpy-sc2"
}

$upstream = $repoMap[$Repo]
$vendorDir = Join-Path (Split-Path $PSScriptRoot -Parent) "vendor" $Repo
$tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) "voicecraft_vendor_$Repo"

Write-Host "Syncing $Repo from $upstream ..."

# 清理临时目录
if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }

# clone
git clone --depth 1 $upstream $tmpDir
if (-not $?) { Write-Error "git clone failed"; exit 1 }

# 读 commit hash
$commitHash = git -C $tmpDir rev-parse HEAD
Write-Host "Upstream commit: $commitHash"

# 删 vendor 目标目录，重建
if (Test-Path $vendorDir) { Remove-Item $vendorDir -Recurse -Force }
Copy-Item $tmpDir $vendorDir -Recurse

# 删 .git
$dotGit = Join-Path $vendorDir ".git"
if (Test-Path $dotGit) { Remove-Item $dotGit -Recurse -Force }

# 更新 ATTRIBUTION.md
$attrFile = Join-Path $vendorDir "ATTRIBUTION.md"
if (Test-Path $attrFile) {
    $today = (Get-Date).ToString("yyyy-MM-dd")
    $content = Get-Content $attrFile -Raw
    $content = $content -replace "(?m)^- \*\*Cloned commit\*\*:.*$", "- **Cloned commit**: ``$commitHash``"
    $content = $content -replace "(?m)^- \*\*Clone date\*\*:.*$", "- **Clone date**: $today"
    Set-Content $attrFile $content -Encoding utf8
    Write-Host "Updated ATTRIBUTION.md"
}

# 清理临时目录
Remove-Item $tmpDir -Recurse -Force

Write-Host "Done. vendor/$Repo synced to $commitHash"
