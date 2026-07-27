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
    [switch]$NoRealtime,
    # Quality-first video: lower framerate so each frame gets more bits ->
    # sharper picture on a bad network (trade-off: choppier motion).
    [switch]$Quality,
    # Explicit target video FPS override (wins over -Quality). 0 = unset.
    [int]$VideoFps = 0,
    # Admin dashboard token. Min length 8 (2026-06-20: lowered from 16 for testing).
    # DEFAULT: admin is ON. If -AdminToken is empty, the token is auto-sourced (see below):
    #   1) -AdminToken explicit  2) $env:VIBECRAFT_ADMIN_TOKEN  3) .secrets/admin-token.txt
    # Example: .\start.ps1 -AdminToken "my-secret-admin-key"
    [string]$AdminToken = "",
    # Opt out of default admin (admin dashboard stays off).
    [switch]$NoAdmin,
    # Named server config: resolves to config/servers/<name>.yaml and passes
    # --config to vibecraft serve. CLI flags (Token/Ip/Port) that are NOT
    # explicitly bound override the file only when supplied; unbound defaults
    # do NOT clobber the config file's values.
    # Example: .\start.ps1 -ServerName close_test
    [string]$ServerName = ""
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

# Video quality-first mode (webrtc.py reads these env vars at import).
if ($Quality) {
    $env:VIBECRAFT_VIDEO_QUALITY = "1"
    Write-Host "Video quality-first mode ON (lower fps, sharper picture)." -ForegroundColor Cyan
}
if ($VideoFps -gt 0) {
    $env:VIBECRAFT_VIDEO_FPS = "$VideoFps"
    Write-Host "Video target FPS = $VideoFps" -ForegroundColor Cyan
}

# Build 'vibecraft serve' args.
#   --no-sync: use the venv AS-IS, do NOT re-sync to the lockfile. Critical because
#     FunASR's torch/torchaudio were installed manually (uv pip install) and are NOT
#     in uv.lock -> a sync would treat them as extraneous and REMOVE them, killing
#     voice input (funasr import needs torch). The venv already has dev+sc2+asr+torch
#     installed, so no sync is needed. (If you ever change real deps, run
#     `uv sync --extra dev --extra sc2 --extra asr` manually, then add torch to the
#     lock before dropping --no-sync.)
#   --extra flags kept for documentation / in case --no-sync is removed later.
$ServeArgs = @(
    "run", "--no-sync", "--extra", "dev", "--extra", "sc2", "--extra", "asr",
    "vibecraft", "serve"
)

if ($ServerName -ne "") {
    # Named server config: pass --config; let the YAML file provide port/token/ip/host.
    # Only add explicit CLI flags if the user actually bound them (not default values),
    # so the config file values are not clobbered by start.ps1 defaults.
    $ConfigPath = Join-Path $RepoRoot "config\servers\$ServerName.yaml"
    $ServeArgs += @("--config", $ConfigPath)
    if ($PSBoundParameters.ContainsKey('Port')) { $ServeArgs += @("--port", $Port) }
    if ($PSBoundParameters.ContainsKey('Token') -and $Token -ne "") { $ServeArgs += @("--token", $Token) }
    if ($PSBoundParameters.ContainsKey('Ip') -and $Ip -ne "") { $ServeArgs += @("--ip", $Ip) }
} else {
    # Original behavior: always pass --port; conditionally pass token/ip.
    $ServeArgs += @("--port", $Port)
    if ($Token -ne "") { $ServeArgs += @("--token", $Token) }
    if ($Ip -ne "") { $ServeArgs += @("--ip", $Ip) }
}

if ($NoRealtime) { $ServeArgs += @("--no-realtime") }

# Admin dashboard: ON by default (2026-06-20 用户). Source token by precedence unless -NoAdmin.
#   1) explicit -AdminToken  2) $env:VIBECRAFT_ADMIN_TOKEN  3) .secrets/admin-token.txt
# 机密只在本地 .secrets/（.gitignore 排除）；脚本不回显 token 明文。
if (-not $NoAdmin) {
    if ($AdminToken -eq "") {
        if ($env:VIBECRAFT_ADMIN_TOKEN) {
            $AdminToken = $env:VIBECRAFT_ADMIN_TOKEN
        } else {
            $AdminTokenFile = Join-Path $RepoRoot ".secrets\admin-token.txt"
            if (Test-Path $AdminTokenFile) {
                $AdminToken = (Get-Content $AdminTokenFile -Raw).Trim()
            }
        }
    }
    if ($AdminToken -ne "") {
        $ServeArgs += @("--admin-token", $AdminToken)
        Write-Host "Admin dashboard: ON (token sourced; >=8 chars required)" -ForegroundColor DarkGray
    } else {
        Write-Host "Admin dashboard: OFF (no token found; set .secrets\admin-token.txt or -AdminToken / -NoAdmin)" -ForegroundColor Yellow
    }
} elseif ($AdminToken -ne "") {
    # -NoAdmin wins: explicitly keep admin off even if a token was passed.
    Write-Host "Admin dashboard: OFF (-NoAdmin)" -ForegroundColor DarkGray
}

Write-Host "VibeCraft starting... (Ctrl+C to stop)" -ForegroundColor Cyan
Write-Host ""

& uv @ServeArgs
