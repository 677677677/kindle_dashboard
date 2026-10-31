$ErrorActionPreference = 'Stop'

$runtimeRoot = Join-Path $env:LOCALAPPDATA 'TimeMirror'
$homePath = Join-Path $runtimeRoot 'syncthing'
$syncPath = Join-Path $runtimeRoot 'ActivityWatchSync'
$taskName = 'TimeMirror-Syncthing'

function Find-SyncthingExecutable {
    $command = Get-Command syncthing.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }

    $packageRoot = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    $candidates = Get-ChildItem -LiteralPath $packageRoot -Filter syncthing.exe -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like '*Syncthing.Syncthing*' } |
        Sort-Object -Property LastWriteTimeUtc -Descending
    if ($candidates) { return $candidates[0].FullName }
    return $null
}

$syncthing = Find-SyncthingExecutable
if (-not $syncthing) {
    winget install --id Syncthing.Syncthing --exact --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -ne 0) { throw 'Syncthing 安装失败。' }
    $syncthing = Find-SyncthingExecutable
}
if (-not $syncthing) { throw 'Syncthing 已安装，但没有找到程序文件。' }

New-Item -ItemType Directory -Force -Path $homePath, $syncPath | Out-Null
$identityFiles = @('config.xml', 'cert.pem', 'key.pem') | ForEach-Object { Join-Path $homePath $_ }
if ($identityFiles | Where-Object { -not (Test-Path -LiteralPath $_) }) {
    & $syncthing --config $homePath --data $homePath generate | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Syncthing 配置初始化失败。' }
}

$arguments = "--config `"$homePath`" --data `"$homePath`" serve --no-browser --no-restart --log-file=default"
$action = New-ScheduledTaskAction -Execute $syncthing -Argument $arguments -WorkingDirectory (Split-Path -Parent $syncthing)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Time Mirror 手机数据目录同步' -Force | Out-Null

Start-ScheduledTask -TaskName $taskName
$deviceId = & $syncthing --config $homePath --data $homePath device-id
foreach ($attempt in 1..20) {
    try {
        [xml]$configuration = Get-Content -LiteralPath (Join-Path $homePath 'config.xml') -Raw
        $headers = @{ 'X-API-Key' = [string]$configuration.configuration.gui.apikey }
        $status = Invoke-RestMethod -Headers $headers -TimeoutSec 1 http://127.0.0.1:8384/rest/system/status
        if ($status.myID -eq $deviceId) { break }
    }
    catch {
        Start-Sleep -Milliseconds 500
    }
}

Write-Host '电脑端 Syncthing 已配置为登录后自动运行。'
Write-Host "同步目录：$syncPath"
Write-Host "电脑设备 ID：$deviceId"
Write-Host '本机管理页：http://127.0.0.1:8384/'

