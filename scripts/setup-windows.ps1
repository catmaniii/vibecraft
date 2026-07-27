# VibeCraft one-click local setup (Windows).
#
# What it does (idempotent, safe to re-run):
#   1. Locate your StarCraft II install (SC2PATH env -> ExecuteInfo.txt -> registry
#      -> common dirs) and persist SC2PATH as a user env var.
#   2. Fix the "idle -> live view goes black" issue: never turn off the display /
#      never sleep on AC + disable screensaver (display-off stops SC2 rendering ->
#      black screen capture).
#   3. Check uv (the package manager) and the LLM API key, with guidance if missing.
#
# Prerequisite: StarCraft II already installed (launch it once so ExecuteInfo.txt
# exists -- that is the most reliable way to auto-find it).
#
# Usage:  open PowerShell in the repo root and run:
#     .\scripts\setup-windows.ps1
#
# NOTE: keep this file ASCII-only. PowerShell 5.1 decodes a BOM-less file with the
# system code page (GBK on zh-CN Windows); non-ASCII bytes break the parser.

$ErrorActionPreference = "Continue"

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "    [!] $msg" -ForegroundColor Yellow }

# ---------------------------------------------------------------------------
# 1. Locate StarCraft II -> set SC2PATH
# ---------------------------------------------------------------------------
function Get-DocsDirs {
    $dirs = @()
    if ($env:USERPROFILE) { $dirs += (Join-Path $env:USERPROFILE 'Documents') }
    if ($env:OneDrive)    { $dirs += (Join-Path $env:OneDrive 'Documents') }
    return ($dirs | Select-Object -Unique)
}

function Test-SC2Root($root) {
    return ($root -and (Test-Path (Join-Path $root 'Versions')))
}

function Find-SC2Root {
    # a. already-set SC2PATH
    if (Test-SC2Root $env:SC2PATH) { return $env:SC2PATH }

    # b. ExecuteInfo.txt (records the last-launched SC2 exe; what python-sc2 uses)
    foreach ($d in Get-DocsDirs) {
        $ei = Join-Path $d 'StarCraft II\ExecuteInfo.txt'
        if (Test-Path $ei) {
            $txt = Get-Content $ei -Raw -ErrorAction SilentlyContinue
            if ($txt -and ($txt -match '=\s*"?([^"\r\n]+)"?')) {
                $exe = $matches[1].Trim()
                $idx = $exe.IndexOf('StarCraft II')
                if ($idx -ge 0) {
                    $root = $exe.Substring(0, $idx + 'StarCraft II'.Length)
                    if (Test-SC2Root $root) { return $root }
                }
            }
        }
    }

    # c. common install dirs
    foreach ($c in @(
        'C:\Program Files (x86)\StarCraft II',
        'C:\Program Files\StarCraft II',
        'D:\Program Files (x86)\StarCraft II',
        'D:\StarCraft II', 'E:\StarCraft II', 'F:\StarCraft II'
    )) { if (Test-SC2Root $c) { return $c } }

    # d. uninstall registry (InstallLocation of "StarCraft II")
    foreach ($hive in @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )) {
        try {
            $hit = Get-ItemProperty $hive -ErrorAction SilentlyContinue |
                   Where-Object { $_.DisplayName -like 'StarCraft II*' -and $_.InstallLocation } |
                   Select-Object -First 1
            if ($hit -and (Test-SC2Root $hit.InstallLocation)) { return $hit.InstallLocation }
        } catch {}
    }
    return $null
}

Write-Step "Locating StarCraft II"
$sc2 = Find-SC2Root
if ($sc2) {
    Write-Ok "Found: $sc2"
    [Environment]::SetEnvironmentVariable('SC2PATH', $sc2, 'User')
    $env:SC2PATH = $sc2
    Write-Ok "SC2PATH set (user env var)"
} else {
    Write-Warn2 "Could not auto-find StarCraft II."
    Write-Warn2 "Launch SC2 once (so ExecuteInfo.txt is created), then re-run this script;"
    Write-Warn2 "or set it manually:  setx SC2PATH `"C:\Program Files (x86)\StarCraft II`""
}

# ---------------------------------------------------------------------------
# 2. Fix idle -> black live view (display off stops SC2 rendering)
# ---------------------------------------------------------------------------
Write-Step "Disabling display/system sleep + screensaver (fixes idle black screen)"
powercfg /change monitor-timeout-ac 0 | Out-Null
powercfg /change standby-timeout-ac 0 | Out-Null
powercfg /change disk-timeout-ac 0    | Out-Null
Set-ItemProperty 'HKCU:\Control Panel\Desktop' -Name ScreenSaveActive -Value '0' -ErrorAction SilentlyContinue
Set-ItemProperty 'HKCU:\Control Panel\Desktop' -Name ScreenSaveTimeOut -Value '0' -ErrorAction SilentlyContinue
Write-Ok "Display never turns off on AC; screensaver off."
Write-Host "    (The server also calls SetThreadExecutionState while streaming as a second layer.)" -ForegroundColor DarkGray
Write-Host "    (If the live view still blacks out, your PC may be LOCKING -- disable auto-lock too.)" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# 3. Toolchain + LLM key checks
# ---------------------------------------------------------------------------
Write-Step "Checking uv (package manager)"
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Ok "uv found"
} else {
    Write-Warn2 "uv not found. Install it, then re-run:"
    Write-Host  '    powershell -c "irm https://astral.sh/uv/install.ps1 | iex"' -ForegroundColor White
}

Write-Step "Checking LLM API key (for voice/text command parsing)"
$key = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY', 'User')
if ($key) {
    Write-Ok "DEEPSEEK_API_KEY is set"
} else {
    Write-Warn2 "DEEPSEEK_API_KEY not set -- voice/text commands will fail to parse."
    Write-Host  '    Set it:  setx DEEPSEEK_API_KEY "your-key-here"' -ForegroundColor White
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "================ setup done ================" -ForegroundColor Cyan
Write-Host "Next:  start the server and connect from your phone:" -ForegroundColor White
Write-Host '    .\scripts\start.ps1 -Token vibecraft-dev' -ForegroundColor White
Write-Host "Then scan the QR code / open the printed URL on your phone (same wifi)." -ForegroundColor White
Write-Host "For internet play (China-reachable), see README -> Deployment -> Cloud." -ForegroundColor White
Write-Host "===========================================" -ForegroundColor Cyan
