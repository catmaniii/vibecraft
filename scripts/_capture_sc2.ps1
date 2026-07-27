# Capture the biggest visible SC2_x64 window to $env:SC2_SHOT_OUT (ASCII-only: PS 5.1 misreads UTF-8-no-BOM).
$OutPath = $env:SC2_SHOT_OUT
if ([string]::IsNullOrEmpty($OutPath)) { Write-Output "NO_OUTPATH"; exit 1 }
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class CapSc2 {
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumWindowsProc f, IntPtr l);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  public static void ForceFront(IntPtr h){
    ShowWindow(h, 9); // SW_RESTORE
    BringWindowToTop(h);
    SetForegroundWindow(h);
    // TOPMOST 切换：强制置顶 z-order（后台进程也能生效），再取消 topmost 复原
    SetWindowPos(h, (IntPtr)(-1), 0,0,0,0, 0x0001|0x0002); // HWND_TOPMOST | NOMOVE|NOSIZE
    SetWindowPos(h, (IntPtr)(-2), 0,0,0,0, 0x0001|0x0002); // HWND_NOTOPMOST
  }
  public struct RECT { public int L,T,R,B; }
  delegate bool EnumWindowsProc(IntPtr h, IntPtr l);
  static IntPtr Best; static uint Tgt; static int BestArea;
  static bool Cb(IntPtr h, IntPtr l){ uint pid; GetWindowThreadProcessId(h,out pid);
    if(pid==Tgt && IsWindowVisible(h)){ RECT r; GetWindowRect(h,out r); int a=(r.R-r.L)*(r.B-r.T);
      if(a>BestArea){BestArea=a; Best=h;} } return true; }
  public static IntPtr FindBiggest(uint pid){ Tgt=pid; Best=IntPtr.Zero; BestArea=0; EnumWindows(Cb, IntPtr.Zero); return Best; }
}
"@
Add-Type -AssemblyName System.Drawing
$p = Get-Process SC2_x64 -EA SilentlyContinue | Select-Object -First 1
if (-not $p) { Write-Output "NO_SC2"; exit 1 }
$h = [CapSc2]::FindBiggest([uint32]$p.Id)
if ($h -eq [IntPtr]::Zero) { Write-Output "NO_WINDOW"; exit 1 }
[CapSc2]::ForceFront($h)
Start-Sleep -Milliseconds 1200
$r = New-Object CapSc2+RECT
[CapSc2]::GetWindowRect($h, [ref]$r) | Out-Null
$w = $r.R - $r.L; $ht = $r.B - $r.T
if ($w -le 0 -or $ht -le 0) { Write-Output "BAD_RECT"; exit 1 }
# 先 PrintWindow（直接抓窗口内容，不依赖窗口在屏幕最前）
$bmp = New-Object System.Drawing.Bitmap $w, $ht
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
$ok = [CapSc2]::PrintWindow($h, $hdc, 2)  # PW_RENDERFULLCONTENT
$g.ReleaseHdc($hdc)
$g.Dispose()
# 检测是否几乎全黑/全同色（PrintWindow 对 GPU 渲染窗口可能失败）→ 回退 CopyFromScreen
$px = $bmp.GetPixel([int]($w/2), [int]($ht/2))
$px2 = $bmp.GetPixel([int]($w/4), [int]($ht/3))
if (-not $ok -or ($px.R -eq 0 -and $px.G -eq 0 -and $px.B -eq 0 -and $px2.R -eq 0)) {
  $g2 = [System.Drawing.Graphics]::FromImage($bmp)
  $g2.CopyFromScreen($r.L, $r.T, 0, 0, $bmp.Size)
  $g2.Dispose()
  Write-Output "OK(screen) ${w}x${ht} -> $OutPath"
} else {
  Write-Output "OK(printwin) ${w}x${ht} -> $OutPath"
}
$bmp.Save($OutPath, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
