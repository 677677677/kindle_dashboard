$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$dashboardPath = Join-Path $projectRoot 'dashboard'
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'TimeMirror'
$logPath = Join-Path $runtimeRoot 'logs'
$runtimeOutput = Join-Path $logPath 'runtime.out.log'
$runtimeError = Join-Path $logPath 'runtime.err.log'
$dashboardBuildOutput = Join-Path $logPath 'dashboard-build.out.log'
$dashboardBuildError = Join-Path $logPath 'dashboard-build.err.log'
$dashboardOutput = Join-Path $logPath 'dashboard.out.log'
$dashboardError = Join-Path $logPath 'dashboard.err.log'
$activityWatchBase = Join-Path $runtimeRoot 'tools\activitywatch-v0.14.1\activitywatch'
$activityWatchProcesses = @()
$runtimeProcess = $null
$dashboardProcess = $null

function Test-HttpEndpoint {
    param([string]$Url)

    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 $Url
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

function Test-ActivityWatchApi {
    return Test-HttpEndpoint 'http://127.0.0.1:5600/api/0/info'
}

function Start-PortableActivityWatch {
    param([string]$BasePath, [string]$LogsPath)

    $server = Start-Process -FilePath (Join-Path $BasePath 'aw-server-rust\aw-server-rust.exe') `
        -ArgumentList @('--host', '127.0.0.1', '--port', '5600') -WorkingDirectory $BasePath `
        -RedirectStandardOutput (Join-Path $LogsPath 'activitywatch-server.out.log') `
        -RedirectStandardError (Join-Path $LogsPath 'activitywatch-server.err.log') `
        -WindowStyle Hidden -PassThru
    foreach ($attempt in 1..40) {
        if (Test-ActivityWatchApi) { break }
        if ($server.HasExited) { throw 'ActivityWatch local server exited early.' }
        Start-Sleep -Milliseconds 250
    }
    if (-not (Test-ActivityWatchApi)) {
        if (-not $server.HasExited) { Stop-Process -Id $server.Id }
        throw 'ActivityWatch local server startup timed out.'
    }

    try {
        $afk = Start-Process -FilePath (Join-Path $BasePath 'aw-watcher-afk\aw-watcher-afk.exe') `
            -ArgumentList @('--host', '127.0.0.1', '--port', '5600') -WorkingDirectory $BasePath `
            -RedirectStandardOutput (Join-Path $LogsPath 'activitywatch-afk.out.log') `
            -RedirectStandardError (Join-Path $LogsPath 'activitywatch-afk.err.log') `
            -WindowStyle Hidden -PassThru
        $window = Start-Process -FilePath (Join-Path $BasePath 'aw-watcher-window\aw-watcher-window.exe') `
            -ArgumentList @('--host', '127.0.0.1', '--port', '5600', '--exclude-title') `
            -WorkingDirectory $BasePath `
            -RedirectStandardOutput (Join-Path $LogsPath 'activitywatch-window.out.log') `
            -RedirectStandardError (Join-Path $LogsPath 'activitywatch-window.err.log') `
            -WindowStyle Hidden -PassThru
    }
    catch {
        foreach ($startedProcess in @($window, $afk, $server)) {
            if ($startedProcess -and -not $startedProcess.HasExited) { Stop-Process -Id $startedProcess.Id }
        }
        throw
    }
    return @($server, $afk, $window)
}

try {
    # 仅在启动时请求补读；日历任务内部按日期跳过已完成的同步。
    $calendarTask = Get-ScheduledTask -TaskName 'TimeMirror-Outlook-Calendar' -ErrorAction SilentlyContinue
    if ($calendarTask -and $calendarTask.State -eq 'Ready') {
        Start-ScheduledTask -TaskName 'TimeMirror-Outlook-Calendar'
    }
}
catch {
    Write-Warning '未能请求日历补读，Dashboard 将继续使用上次成功缓存。'
}

$stackState = @(
    (Test-ActivityWatchApi),
    (Test-HttpEndpoint 'http://127.0.0.1:8765/health'),
    (Test-HttpEndpoint 'http://127.0.0.1:3000/')
)
if ($stackState -notcontains $false) {
    Write-Host 'Time Mirror 已在运行。'
    return
}
if ($stackState -contains $true) {
    throw 'Time Mirror 仅部分服务在运行；请先停止残留进程后再启动。'
}

if (-not (Test-Path -LiteralPath $pythonPath)) {
    py -3.12 -m venv (Join-Path $projectRoot '.venv')
}

& $pythonPath -m pip install --disable-pip-version-check --quiet --editable $projectRoot

New-Item -ItemType Directory -Force -Path $logPath | Out-Null
if (-not (Test-ActivityWatchApi) -and (Test-Path -LiteralPath $activityWatchBase)) {
    try {
        $activityWatchProcesses = @(Start-PortableActivityWatch -BasePath $activityWatchBase -LogsPath $logPath)
    }
    catch {
        Write-Warning "ActivityWatch failed to start; computer apps will remain disconnected: $($_.Exception.Message)"
        foreach ($ownedProcess in $activityWatchProcesses) {
            if ($ownedProcess -and -not $ownedProcess.HasExited) { Stop-Process -Id $ownedProcess.Id }
        }
        $activityWatchProcesses = @()
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $dashboardPath 'node_modules'))) {
    Push-Location $dashboardPath
    try {
        npm install --no-audit --no-fund
    }
    finally {
        Pop-Location
    }
}

$nodePath = (Get-Command node.exe -ErrorAction Stop).Source
$vinextCli = Join-Path $dashboardPath 'node_modules\vinext\dist\cli.js'
if (-not (Test-Path -LiteralPath $vinextCli)) {
    throw "没有找到 Dashboard 构建入口：$vinextCli"
}

$buildProcess = Start-Process -FilePath $nodePath -ArgumentList @($vinextCli, 'build') `
    -WorkingDirectory $dashboardPath -RedirectStandardOutput $dashboardBuildOutput `
    -RedirectStandardError $dashboardBuildError -WindowStyle Hidden -Wait -PassThru
if ($buildProcess.ExitCode -ne 0) {
    throw "Dashboard 构建失败。请检查 $dashboardBuildError"
}

$runtimeProcess = Start-Process `
    -FilePath $pythonPath `
    -ArgumentList @('-m', 'time_mirror', 'serve', '--port', '8765', '--interval', '300') `
    -RedirectStandardOutput $runtimeOutput `
    -RedirectStandardError $runtimeError `
    -WindowStyle Hidden `
    -PassThru

try {
    $runtimeReady = $false
    foreach ($attempt in 1..200) {
        if ($runtimeProcess.HasExited) {
            throw "Snapshot runtime exited early. Check $runtimeError"
        }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8765/health
            if ($response.StatusCode -eq 200) {
                if ($runtimeProcess.HasExited) {
                    throw "Port 8765 is already in use. Check $runtimeError"
                }
                $runtimeReady = $true
                break
            }
        }
        catch {
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not $runtimeReady) {
        throw "Snapshot runtime failed to start. Check $runtimeError"
    }

    $dashboardProcess = Start-Process -FilePath $nodePath `
        -ArgumentList @($vinextCli, 'start', '--hostname', '0.0.0.0', '--port', '3000') `
        -WorkingDirectory $dashboardPath -RedirectStandardOutput $dashboardOutput `
        -RedirectStandardError $dashboardError -WindowStyle Hidden -PassThru
    foreach ($attempt in 1..120) {
        if ($dashboardProcess.HasExited) {
            throw "Dashboard 提前退出。请检查 $dashboardError"
        }
        if (Test-HttpEndpoint 'http://127.0.0.1:3000/') { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not (Test-HttpEndpoint 'http://127.0.0.1:3000/')) {
        throw "Dashboard 启动超时。请检查 $dashboardError"
    }

    Write-Host 'Time Mirror 已启动：http://127.0.0.1:3000/'
    Wait-Process -Id $dashboardProcess.Id
    if ($dashboardProcess.ExitCode -ne 0) {
        throw "Dashboard 异常退出。请检查 $dashboardError"
    }
}
finally {
    if ($dashboardProcess -and -not $dashboardProcess.HasExited) {
        Stop-Process -Id $dashboardProcess.Id
    }
    if ($runtimeProcess -and -not $runtimeProcess.HasExited) {
        Stop-Process -Id $runtimeProcess.Id
    }
    foreach ($ownedProcess in $activityWatchProcesses) {
        if ($ownedProcess -and -not $ownedProcess.HasExited) {
            Stop-Process -Id $ownedProcess.Id
        }
    }
}

