# VibeCraft PC -> VPS reverse tunnel.
# Exposes local server (8080) on VPS 127.0.0.1:18080; VPS nginx reverse-proxies it
# to public https://app.<VPS_IP>.sslip.io/. Auto-reconnects on drop.
#
# Usage:  .\deploy\turn\pc-tunnel.ps1
# Autostart: register as a Scheduled Task (at logon), action = powershell -File <this>.
#
# NOTE: keep this file ASCII-only. PowerShell 5.1 decodes a BOM-less file with the
# system code page (GBK on zh-CN Windows); non-ASCII bytes break the parser.
param(
    [string]$Key,
    [string]$VpsHost,
    [int]$RemotePort = 18080,
    [int]$LocalPort = 8080
)
$ErrorActionPreference = "Continue"

# Real values come from a gitignored config (template: deploy\turn\vibecraft-turn.env.example;
# actual file .secrets\vibecraft-turn.env is gitignored, never committed).
# Command-line -Key/-VpsHost take precedence.
$envFile = if ($env:VIBECRAFT_TURN_ENV) { $env:VIBECRAFT_TURN_ENV } else { ".secrets\vibecraft-turn.env" }
$cfg = @{}
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"?([^"#]*?)"?\s*(#.*)?$') {
            $cfg[$matches[1]] = $matches[2].Trim()
        }
    }
}
if (-not $Key)     { $Key = if ($cfg.SSH_KEY) { $cfg.SSH_KEY } else { ".secrets\your-vps-key.pem" } }
if (-not $VpsHost) {
    $u  = if ($cfg.SSH_USER) { $cfg.SSH_USER } else { "root" }
    $ip = if ($cfg.VPS_PUBLIC_IP) { $cfg.VPS_PUBLIC_IP } else { "<VPS_IP>" }
    $VpsHost = "$u@$ip"
}
while ($true) {
    Write-Host ("[tunnel] " + (Get-Date -Format "HH:mm:ss") + " connecting -> $VpsHost (R $RemotePort -> localhost:$LocalPort)")
    ssh -i $Key -N `
        -o ServerAliveInterval=30 -o ServerAliveCountMax=3 `
        -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new `
        -R ("127.0.0.1:{0}:127.0.0.1:{1}" -f $RemotePort, $LocalPort) $VpsHost
    Write-Host ("[tunnel] " + (Get-Date -Format "HH:mm:ss") + " disconnected, retry in 5s...")
    Start-Sleep -Seconds 5
}
