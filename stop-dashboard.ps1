$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'TimeMirror'
$vinextCli = Join-Path $projectRoot 'dashboard\node_modules\vinext\dist\cli.js'
$taskName = 'TimeMirror-Dashboard'

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task -and $task.State -eq 'Running') {
    Stop-ScheduledTask -TaskName $taskName
    Start-Sleep -Milliseconds 500
}

$targets = Get-CimInstance Win32_Process | Where-Object {
    $commandLine = [string]$_.CommandLine
    $executablePath = [string]$_.ExecutablePath
    ($_.Name -eq 'node.exe' -and $commandLine.Contains($vinextCli) -and $commandLine.Contains(' start ')) -or
    ($_.Name -eq 'python.exe' -and $commandLine.Contains('-m time_mirror serve --port 8765')) -or
    ($_.Name -in @('aw-server-rust.exe', 'aw-watcher-afk.exe', 'aw-watcher-window.exe') -and
        $executablePath.StartsWith($runtimeRoot, [System.StringComparison]::OrdinalIgnoreCase))
}

foreach ($target in $targets) {
    Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
}

foreach ($attempt in 1..20) {
    $listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -in 3000, 5600, 8765 }
    if (-not $listeners) { break }
    Start-Sleep -Milliseconds 250
}

$remaining = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in 3000, 5600, 8765 }
if ($remaining) {
    $details = ($remaining | ForEach-Object { "$($_.LocalPort):$($_.OwningProcess)" }) -join '，'
    throw "仍有 Time Mirror 端口未释放：$details"
}

Write-Host 'Time Mirror 已停止。'

