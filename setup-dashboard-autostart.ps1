$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$startScript = Join-Path $projectRoot 'start-dashboard.ps1'
$stopScript = Join-Path $projectRoot 'stop-dashboard.ps1'
$taskName = 'TimeMirror-Dashboard'
$userName = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not (Test-Path -LiteralPath $startScript)) {
    throw "没有找到 Dashboard 启动脚本：$startScript"
}
if (-not (Test-Path -LiteralPath $stopScript)) {
    throw "没有找到 Dashboard 停止脚本：$stopScript"
}

& $stopScript

$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startScript`""
$action = New-ScheduledTaskAction `
    -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' `
    -Argument $arguments `
    -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userName
$principal = New-ScheduledTaskPrincipal -UserId $userName -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'Time Mirror Dashboard 登录自启服务' `
    -Force | Out-Null

Start-ScheduledTask -TaskName $taskName
$ready = $false
foreach ($attempt in 1..180) {
    $task = Get-ScheduledTask -TaskName $taskName
    if ($task.State -ne 'Running') { break }
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 'http://127.0.0.1:3000/'
        if ($response.StatusCode -eq 200) {
            $ready = $true
            break
        }
    }
    catch {
        Start-Sleep -Milliseconds 500
    }
}

if (-not $ready) {
    $result = (Get-ScheduledTaskInfo -TaskName $taskName).LastTaskResult
    throw "Dashboard 自启验证失败，计划任务结果：$result"
}

Write-Host 'Dashboard 已配置为 Windows 登录后自动运行。'
Write-Host '本机地址：http://127.0.0.1:3000/'

