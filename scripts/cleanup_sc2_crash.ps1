# SC2 崩溃弹窗周期清理脚本（由 Windows 计划任务定期调用）。
#
# 背景：跑 build_efficiency / addon_selftest 等真局自测会反复起 SC2；崩溃时 Blizzard
# 会留一个 "BlizzardError"（窗口标题 "StarCraft II"）崩溃上报弹窗 + 可能的 WerFault
# 错误上报进程，堆在桌面上。本脚本定期清掉它们。
#
# 安全：只杀 **崩溃弹窗类**进程（BlizzardError / WerFault*），这些只在崩溃时存在，
# 绝不碰正在跑的真局（真局是 SC2_x64 且 Responding=true）。默认 **不** 杀 SC2_x64。
#
# 手动跑：powershell -ExecutionPolicy Bypass -File scripts\cleanup_sc2_crash.ps1
# 计划任务：scripts\install_sc2_cleanup_task.ps1 注册（每 5 分钟一次）。

$ErrorActionPreference = 'SilentlyContinue'
$log = Join-Path $PSScriptRoot '..\logs\sc2_crash_cleanup.log'
$killed = @()

foreach ($name in @('BlizzardError', 'WerFault', 'WerFaultSecure')) {
    Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
        # WerFault 兜底：只清跟 SC2/Blizzard 相关的崩溃上报，别误清别的程序的 WerFault。
        $isSc2 = ($_.ProcessName -eq 'BlizzardError') -or
                 ($_.MainWindowTitle -match 'StarCraft|SC2|Blizzard')
        if ($isSc2) {
            $killed += "$($_.ProcessName)(pid=$($_.Id),title='$($_.MainWindowTitle)')"
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

if ($killed.Count -gt 0) {
    $line = "{0}  cleaned: {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), ($killed -join '; ')
    Add-Content -Path $log -Value $line -Encoding utf8
    Write-Output $line
}
else {
    Write-Output ("{0}  no SC2 crash window" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
}
