param(
    [string]$RainmeterPath = (Join-Path $env:ProgramFiles 'Rainmeter\Rainmeter.exe'),
    [string]$RuntimeRoot = (Join-Path $env:USERPROFILE '.time-mirror\desktop'),
    [int]$TimeoutSeconds = 900
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
$logPath = Join-Path $RuntimeRoot 'startup.log'
Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "$(Get-Date -Format s) 开始等待桌面页面。"
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$ready = $false
do {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 'http://127.0.0.1:3000/widgets/remaining'
        $ready = $response.StatusCode -eq 200
    }
    catch { $ready = $false }
    if (-not $ready) { Start-Sleep -Seconds 3 }
} while (-not $ready -and (Get-Date) -lt $deadline)
if (-not $ready) {
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "$(Get-Date -Format s) 桌面页面尚未就绪；登录任务将按重试策略再运行。"
    exit 1
}
if (-not (Get-Process Rainmeter -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $RainmeterPath -WindowStyle Hidden
    Start-Sleep -Seconds 2
}
$rainmeterProcess = @(Get-Process Rainmeter -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $RainmeterPath })
if ($rainmeterProcess.Count -ne 1) {
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "$(Get-Date -Format s) 无法确认唯一 Rainmeter 进程，未发出启动命令。"
    exit 1
}
$rainmeterId = $rainmeterProcess[0].Id
foreach ($name in @('Remaining', 'Overview', 'Allocation', 'Axis')) {
    if (-not (Get-Process -Id $rainmeterId -ErrorAction SilentlyContinue)) {
        Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "$(Get-Date -Format s) Rainmeter 在组件启动期间退出，等待任务重试。"
        exit 1
    }
    & $RainmeterPath '!ActivateConfig' "TimeMirror\$name" "$name.ini"
    & $RainmeterPath '!CommandMeasure' 'WebView' 'WebView Start' "TimeMirror\$name"
    Start-Sleep -Milliseconds 300
}
foreach ($attempt in 1..10) {
    Start-Sleep -Seconds 1
    if (-not (Get-Process -Id $rainmeterId -ErrorAction SilentlyContinue)) {
        Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "$(Get-Date -Format s) Rainmeter 未通过启动后进程持续性检查。"
        exit 1
    }
}
Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "$(Get-Date -Format s) 页面已就绪，四块已请求幂等启动；Rainmeter 进程通过持续十秒检查。"
