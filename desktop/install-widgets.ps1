param(
    [string]$LocalPlugin,
    [string]$RainmeterPath = (Join-Path $env:ProgramFiles 'Rainmeter\Rainmeter.exe'),
    [int]$ScreenWidth = 0,
    [int]$ScreenHeight = 0,
    [double]$Scale = 0,
    [double]$DpiScale = 0,
    [double]$WebZoomFactor = 0
)
$ErrorActionPreference = 'Stop'
$desktopRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$configRoot = Join-Path $env:APPDATA 'Rainmeter'
$configPath = Join-Path $configRoot 'Rainmeter.ini'
$pluginPath = Join-Path $configRoot 'Plugins\WebView2.dll'
$runtimeRoot = Join-Path $env:USERPROFILE '.time-mirror\desktop'
$backupPath = Join-Path $runtimeRoot ('backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
$utf8Bom = New-Object System.Text.UTF8Encoding($true)

function Get-ExecutableArchitecture([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $reader = New-Object IO.BinaryReader($stream)
    try {
        $stream.Position = 0x3c
        $stream.Position = $reader.ReadInt32() + 4
        $machine = $reader.ReadUInt16()
        if ($machine -eq 0x8664) { return 'x64' }
        if ($machine -eq 0x014c) { return 'x32' }
        throw '无法识别 Rainmeter 程序位数。'
    }
    finally { $reader.Dispose(); $stream.Dispose() }
}

function Set-IniSection([string]$Text, [string]$Name, [string]$Body) {
    $pattern = '(?ms)^\[' + [regex]::Escape($Name) + '\][^\r\n]*\r?\n.*?(?=^\[|\z)'
    $replacement = '[' + $Name + "]`r`n" + $Body.Trim() + "`r`n`r`n"
    if ([regex]::IsMatch($Text, $pattern)) {
        return [regex]::Replace($Text, $pattern, [System.Text.RegularExpressions.MatchEvaluator]{ param($m) $replacement })
    }
    return $Text.TrimEnd() + "`r`n`r`n" + $replacement
}

function Get-PhysicalScreen {
    Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
public static class TimeMirrorScreen {
    [StructLayout(LayoutKind.Sequential)] public struct Point { public int X; public int Y; }
    [DllImport("user32.dll")] public static extern System.IntPtr GetDC(System.IntPtr hwnd);
    [DllImport("user32.dll")] public static extern int ReleaseDC(System.IntPtr hwnd, System.IntPtr dc);
    [DllImport("user32.dll")] public static extern System.IntPtr MonitorFromPoint(Point point, uint flags);
    [DllImport("shcore.dll")] public static extern int GetScaleFactorForMonitor(System.IntPtr monitor, out int factor);
    [DllImport("gdi32.dll")] public static extern int GetDeviceCaps(System.IntPtr dc, int index);
}
'@
    $dc = [TimeMirrorScreen]::GetDC([IntPtr]::Zero)
    try {
        $factor = 100
        $point = New-Object TimeMirrorScreen+Point
        $monitor = [TimeMirrorScreen]::MonitorFromPoint($point, 1)
        [void][TimeMirrorScreen]::GetScaleFactorForMonitor($monitor, [ref]$factor)
        return @([TimeMirrorScreen]::GetDeviceCaps($dc, 118), [TimeMirrorScreen]::GetDeviceCaps($dc, 117), ($factor / 100.0))
    }
    finally { [void][TimeMirrorScreen]::ReleaseDC([IntPtr]::Zero, $dc) }
}

if (-not (Test-Path -LiteralPath $RainmeterPath)) { throw '请先安装 Rainmeter 4.5 或更新版本。' }
$version = (Get-Item $RainmeterPath).VersionInfo
if ($version.FileMajorPart -lt 4 -or ($version.FileMajorPart -eq 4 -and $version.FileMinorPart -lt 5)) {
    throw 'Rainmeter 版本过低，需要 4.5 或更新版本。'
}
$architecture = Get-ExecutableArchitecture $RainmeterPath
$expectedDllHashes = @{
    x64 = '978aa21805cc837f6ee5456822e49345ad3a1680dd2fd30462d48421037ff02f'
    x32 = 'ccf7092f29e59a71539de877cf8a2243826c098d817bec6e376c5babdd930697'
}
New-Item -ItemType Directory -Force -Path $runtimeRoot, $backupPath | Out-Null
$startupTaskName = 'TimeMirror-Desktop-Widgets'
$previousTask = Get-ScheduledTask -TaskName $startupTaskName -ErrorAction SilentlyContinue
if ($previousTask) {
    Export-ScheduledTask -TaskName $startupTaskName | Set-Content -LiteralPath (Join-Path $backupPath 'startup-task.xml') -Encoding Unicode
}
if (-not $LocalPlugin) {
    $archive = Join-Path $runtimeRoot 'WebView2-v1.0.0.zip'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/NSTechBytes/WebView2/releases/download/v1.0.0/WebView2_v1.0.0_x64_x86_dll.zip' -OutFile $archive
    if ((Get-FileHash $archive -Algorithm SHA256).Hash -ne 'f1b207eb61cfd3487cab39be3b176310af464104eb9f385357617b4fbf23e80c') {
        throw '官方插件压缩包校验失败，未安装。'
    }
    $unpacked = Join-Path $runtimeRoot 'plugin-v1.0.0'
    Expand-Archive -LiteralPath $archive -DestinationPath $unpacked -Force
    $LocalPlugin = Join-Path $unpacked "$architecture\WebView2.dll"
}
if ((Get-FileHash -LiteralPath $LocalPlugin -Algorithm SHA256).Hash -ne $expectedDllHashes[$architecture]) {
    throw '插件 DLL 与已核实的官方版本不一致，未安装。'
}
if ($ScreenWidth -le 0 -or $ScreenHeight -le 0 -or $DpiScale -le 0) {
    $screen = Get-PhysicalScreen
    if ($ScreenWidth -le 0) { $ScreenWidth = $screen[0] }
    if ($ScreenHeight -le 0) { $ScreenHeight = $screen[1] }
    if ($DpiScale -le 0) { $DpiScale = $screen[2] }
}
if ($Scale -le 0) { $Scale = [Math]::Min($ScreenWidth / 1649.0, $ScreenHeight / 928.0) }
if ($Scale -lt 0.5 -or $Scale -gt 4) { throw '屏幕比例超出可支持范围，请显式指定 Scale。' }
if ($DpiScale -lt 1 -or $DpiScale -gt 4) { throw 'DPI 比例超出可支持范围。' }
$unitScale = $Scale / $DpiScale
if ($WebZoomFactor -le 0) { $WebZoomFactor = $unitScale }

# 先停止保存配置，再备份；失败时不强制结束其他进程。
$running = @(Get-Process Rainmeter -ErrorAction SilentlyContinue)
if ($running.Count) {
    & $RainmeterPath '!Quit'
    foreach ($process in $running) {
        if (-not $process.WaitForExit(5000)) { throw 'Rainmeter 未正常退出，未覆盖配置。' }
    }
}
$text = if (Test-Path -LiteralPath $configPath) { [IO.File]::ReadAllText($configPath) } else { "[Rainmeter]`r`n" }
if (Test-Path -LiteralPath $configPath) { Copy-Item -LiteralPath $configPath -Destination (Join-Path $backupPath 'Rainmeter.ini') }
$skinMatch = [regex]::Match($text, '(?im)^SkinPath=(.+)$')
$skinRoot = if ($skinMatch.Success) { $skinMatch.Groups[1].Value.Trim() } else { Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Rainmeter\Skins' }
$targetRoot = Join-Path $skinRoot 'TimeMirror'
if (Test-Path -LiteralPath $targetRoot) { Copy-Item -LiteralPath $targetRoot -Destination (Join-Path $backupPath 'TimeMirror') -Recurse }
if (Test-Path -LiteralPath $pluginPath) { Copy-Item -LiteralPath $pluginPath -Destination (Join-Path $backupPath 'WebView2.dll') }

New-Item -ItemType Directory -Force -Path $configRoot, (Split-Path $pluginPath), $skinRoot | Out-Null
Copy-Item -LiteralPath $LocalPlugin -Destination $pluginPath -Force
Copy-Item -LiteralPath (Join-Path $desktopRoot 'skins\TimeMirror') -Destination $skinRoot -Recurse -Force
$scaleText = $Scale.ToString('0.000000', [Globalization.CultureInfo]::InvariantCulture)
$zoomText = $WebZoomFactor.ToString('0.000000', [Globalization.CultureInfo]::InvariantCulture)
if (-not $skinMatch.Success) { $text = $text -replace '(?im)^\[Rainmeter\]', "[Rainmeter]`r`nSkinPath=$skinRoot\" }

# 只停用新装 Rainmeter 的示范皮肤，其他自定义皮肤保持原样。
foreach ($name in @('Clock', 'Disk', 'System', 'Welcome')) {
    $section = 'illustro\' + $name
    $pattern = '(?ms)(^\[' + [regex]::Escape($section) + '\][^\r\n]*\r?\n)(.*?)(?=^\[|\z)'
    $text = [regex]::Replace($text, $pattern, [System.Text.RegularExpressions.MatchEvaluator]{
        param($match)
        $body = $match.Groups[2].Value -replace '(?im)^Active=.*$', 'Active=0'
        return $match.Groups[1].Value + $body
    })
}
$layout = @(
    @{ Name='Remaining'; X=355; Y=35; W=290; H=150 }, @{ Name='Overview'; X=1100; Y=40; W=250; H=165 },
    @{ Name='Allocation'; X=1100; Y=230; W=250; H=390 }, @{ Name='Axis'; X=355; Y=805; W=980; H=70 }
)
foreach ($panel in $layout) {
    $x = [Math]::Round($panel.X * $unitScale); $y = [Math]::Round($panel.Y * $unitScale)
    $width = [Math]::Round($panel.W * $unitScale); $height = [Math]::Round($panel.H * $unitScale)
    $skinFile = Join-Path $targetRoot ($panel.Name + '\' + $panel.Name + '.ini')
    $skinText = [IO.File]::ReadAllText($skinFile)
    $skinText = $skinText -replace '(?m)^W=.*$', "W=$width" -replace '(?m)^H=.*$', "H=$height" -replace '(?m)^ZoomFactor=.*$', "ZoomFactor=$zoomText"
    [IO.File]::WriteAllText($skinFile, $skinText, [Text.Encoding]::Unicode)
    $body = "Active=1`r`nWindowX=$x`r`nWindowY=$y`r`nAlwaysOnTop=-2`r`nSavePosition=1`r`nDraggable=0`r`nClickThrough=1`r`nKeepOnScreen=1`r`nSnapEdges=0`r`nAlphaValue=255`r`nFadeDuration=0"
    $text = Set-IniSection $text ('TimeMirror\' + $panel.Name) $body
}
[IO.File]::WriteAllText($configPath, $text, [Text.Encoding]::Unicode)
$metadata = @{ skinRoot=$skinRoot; rainmeterPath=$RainmeterPath; screenWidth=$ScreenWidth; screenHeight=$ScreenHeight; scale=$Scale; dpiScale=$DpiScale; unitScale=$unitScale; webZoomFactor=$WebZoomFactor; backup=$backupPath; startupTask=$startupTaskName }
[IO.File]::WriteAllText((Join-Path $runtimeRoot 'installation.json'), ($metadata | ConvertTo-Json), $utf8Bom)
[IO.File]::WriteAllText((Join-Path $backupPath 'installation.json'), ($metadata | ConvertTo-Json), $utf8Bom)

$startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'Rainmeter.lnk'
if (-not (Test-Path -LiteralPath $startup)) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($startup)
    $shortcut.TargetPath = $RainmeterPath
    $shortcut.WorkingDirectory = Split-Path $RainmeterPath
    $shortcut.Save()
}
Start-Process -FilePath $RainmeterPath -WindowStyle Hidden
$launcher = Join-Path $desktopRoot 'start-widgets.ps1'
$powershellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $launcher + '" -RainmeterPath "' + $RainmeterPath + '" -RuntimeRoot "' + $runtimeRoot + '"'
$action = New-ScheduledTaskAction -Execute $powershellPath -Argument $arguments -WorkingDirectory $desktopRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2) -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $startupTaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description '等待本机桌面页面后恢复四块只读 Rainmeter 组件。' -Force | Out-Null
Start-ScheduledTask -TaskName $startupTaskName
Write-Host "已安装四个桌面组件，缩放 $scaleText。安装前配置备份：$backupPath"
Write-Host '已注册并启动登录恢复任务；页面就绪后将自动恢复四块显示。'
