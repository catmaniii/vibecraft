# VoiceCraft 一键启动脚本
# 用法：在 PowerShell 里运行 .\scripts\start.ps1
#       （或在文件资源管理器里右键 → 使用 PowerShell 运行；
#        .ps1 直接双击默认是用记事本打开，不会执行）
#
# 可选参数（覆盖默认值）：
#   -Port <int>   监听端口，默认 8080
#   -Token <str>  指定 room_token（默认自动生成）
#   -Ip <str>     二维码显示用 IP（默认自动检测局域网 IP）
#
# 示例：
#   .\scripts\start.ps1
#   .\scripts\start.ps1 -Port 9090
#   .\scripts\start.ps1 -Token "mytoken"

param(
    [int]$Port = 8080,
    [string]$Token = "",
    [string]$Ip = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# 切换到仓库根目录（脚本放在 scripts/ 子目录）
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

# 确认 uv 可用
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "找不到 uv。请先安装：https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
}

# 组装 voicecraft serve 参数
$ServeArgs = @("run", "voicecraft", "serve", "--port", $Port)

if ($Token -ne "") {
    $ServeArgs += @("--token", $Token)
}
if ($Ip -ne "") {
    $ServeArgs += @("--ip", $Ip)
}

Write-Host "VoiceCraft 启动中... (Ctrl+C 停止)" -ForegroundColor Cyan
Write-Host ""

# 运行
& uv @ServeArgs
