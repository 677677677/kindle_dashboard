param([Parameter(Mandatory=$true)][string]$BackupPath)
$ErrorActionPreference = 'Stop'
$backup = [IO.Path]::GetFullPath($BackupPath)
$metadataPath = Join-Path $backup 'installation.json'
$originalConfig = Join-Path $backup 'Rainmeter.ini'
if (-not (Test-Path -LiteralPath $metadataPath) -or -not (Test-Path -LiteralPath $originalConfig)) {
    throw '该目录不是完整的安装备份。'
}
$metadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
$taskName = $metadata.startupTask
if ($taskName) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) { Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue }
    $previousTaskXml = Join-Path $backup 'startup-task.xml'
    if (Test-Path -LiteralPath $previousTaskXml) {
        Register-ScheduledTask -TaskName $taskName -Xml ([IO.File]::ReadAllText($previousTaskXml)) -Force | Out-Null
    }
    elseif ($task) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
}
$rainmeterPath = $metadata.rainmeterPath
$running = @(Get-Process Rainmeter -ErrorAction SilentlyContinue)
if ($running.Count) {
    & $rainmeterPath '!Quit'
    foreach ($process in $running) {
        if (-not $process.WaitForExit(5000)) { throw 'Rainmeter 未正常退出，未恢复配置。' }
    }
}
$configRoot = Join-Path $env:APPDATA 'Rainmeter'
Copy-Item -LiteralPath $originalConfig -Destination (Join-Path $configRoot 'Rainmeter.ini') -Force
if (Test-Path -LiteralPath (Join-Path $backup 'TimeMirror')) {
    Copy-Item -LiteralPath (Join-Path $backup 'TimeMirror') -Destination $metadata.skinRoot -Recurse -Force
}
if (Test-Path -LiteralPath (Join-Path $backup 'WebView2.dll')) {
    Copy-Item -LiteralPath (Join-Path $backup 'WebView2.dll') -Destination (Join-Path $configRoot 'Plugins\WebView2.dll') -Force
}
Start-Process -FilePath $rainmeterPath -WindowStyle Hidden
Write-Host '已恢复安装前的 Rainmeter 布局。新增但未启用的文件保留，便于再次安装。'
