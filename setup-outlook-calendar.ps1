$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$syncScript = Join-Path $projectRoot 'sync-outlook-calendar.ps1'
$localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
$runtimeRoot = Join-Path $localAppData 'TimeMirror'
$userProfile = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
$secretRoot = Join-Path $userProfile '.time-mirror'
$taskName = 'TimeMirror-Outlook-Calendar'
$userName = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not (Test-Path -LiteralPath $pythonPath)) {
    py -3.12 -m venv (Join-Path $projectRoot '.venv')
}
& $pythonPath -m pip install --disable-pip-version-check --quiet --editable $projectRoot
if ($LASTEXITCODE -ne 0) { throw '安装 Outlook 日历依赖失败。' }

& $pythonPath -m time_mirror configure-outlook
if ($LASTEXITCODE -ne 0) { throw '无法启用 Outlook 私有配置。' }
& $syncScript -Force
if ($LASTEXITCODE -ne 0) {
    throw '无法读取 Outlook ICS；请先在 Outlook 网页发布日历并配置订阅地址。'
}

$arguments = '-m time_mirror --runtime-root "{0}" --secret-root "{1}" sync-outlook --quiet' -f $runtimeRoot, $secretRoot
$action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument $arguments `
    -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At '07:00'
$principal = New-ScheduledTaskPrincipal -UserId $userName -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description '每天 07:00 只读同步 Outlook 日历；错过后自动补跑' `
    -Force | Out-Null

Write-Host 'Outlook 日历已接入；每天 07:00 自动读取一次。'
Write-Host '如果电脑当时关机，任务会在下一次可运行时补读。'

