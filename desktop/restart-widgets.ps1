param([string]$RainmeterPath = (Join-Path $env:ProgramFiles 'Rainmeter\Rainmeter.exe'))
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $RainmeterPath)) { throw '没有找到 Rainmeter。' }
$task = Get-ScheduledTask -TaskName 'TimeMirror-Desktop-Widgets' -ErrorAction SilentlyContinue
if ($task) { Start-ScheduledTask -TaskName $task.TaskName }
else {
    $launcher = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'start-widgets.ps1'
    $powershellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $launcher + '" -RainmeterPath "' + $RainmeterPath + '"'
    Start-Process -FilePath $powershellPath -ArgumentList $arguments -WindowStyle Hidden
}
Write-Host '已请求恢复四个桌面组件；页面就绪后自动显示，快照服务与 Kindle 页面保持运行。'
