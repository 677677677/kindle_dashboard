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
foreach ($name in @('Remaining', 'Overview', 'Allocation', 'Axis')) {
    & $RainmeterPath '!ActivateConfig' "TimeMirror\$name" "$name.ini"
    & $RainmeterPath '!Refresh' "TimeMirror\$name"
}
Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "$(Get-Date -Format s) 页面已就绪，四个桌面组件已恢复。"
