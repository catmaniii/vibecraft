# VibeCraft one-click launcher.
# Usage: run  .\scripts\start.ps1  from a PowerShell window.
#   (Double-clicking a .ps1 opens it in Notepad and will NOT run it.)
#
# Optional parameters:
#   -Port <int>     listen port, default 8080
#   -Token <str>    fixed room_token (default: auto-generated)
#   -Ip <str>       IP shown in the QR code (default: auto-detect LAN IP)
#   -NoRealtime     Run SC2 in non-realtime mode (step-paced, faster than 1x,
#                   debug only). Default: realtime on (wall-clock pacing).
#                   PWA start_game.config.realtime overrides this per game.
#
# NOTE: keep this file ASCII-only. PowerShell 5.1 decodes a BOM-less file with
# the system code page (GBK on zh-CN Windows); non-ASCII bytes break the parser.

param(
    [int]$Port = 8080,
    [string]$Token = "",
    [string]$Ip = "",
    [switch]$NoRealtime
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# cd to repo root (this script lives in scripts/)
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

# UTF-8 console output: the QR code uses the solid block char U+2588, which
# renders as garbage under a GBK code page.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

# Check uv is available.
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "uv not found. Install: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
}

# Copy user-level env vars into this process. An already-running process does
# not auto-refresh its env block; the SC2 subprocess / LLM call need these two,
# and the spawned child inherits this process's env.
foreach ($name in @("DEEPSEEK_API_KEY", "SC2PATH")) {
    $val = [Environment]::GetEnvironmentVariable($name, "User")
    if ($val) {
        Set-Item -Path "Env:$name" -Value $val
    }
}
if (-not $env:DEEPSEEK_API_KEY) {
    Write-Host "WARNING: DEEPSEEK_API_KEY not set -- LLM parsing will fail." -ForegroundColor Yellow
}
if (-not $env:SC2PATH) {
    Write-Host "WARNING: SC2PATH not set -- cannot launch SC2 on start_game." -ForegroundColor Yellow
}

# Build 'vibecraft serve' args. --extra dev --extra sc2 keeps the ares stack
# installed; a bare 'uv run' would sync to the pyproject default and uninstall
# ares, breaking start_game.
$ServeArgs = @(
    "run", "--extra", "dev", "--extra", "sc2",
    "vibecraft", "serve", "--port", $Port
)
if ($Token -ne "") { $ServeArgs += @("--token", $Token) }
if ($Ip -ne "") { $ServeArgs += @("--ip", $Ip) }
if ($NoRealtime) { $ServeArgs += @("--no-realtime") }

Write-Host "VibeCraft starting... (Ctrl+C to stop)" -ForegroundColor Cyan
Write-Host ""

& uv @ServeArgs
