param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
$runtimeRoot = Join-Path $localAppData 'TimeMirror'
$logPath = Join-Path $runtimeRoot 'logs'
$outputPath = Join-Path $logPath 'outlook-calendar.out.log'
$errorPath = Join-Path $logPath 'outlook-calendar.err.log'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Time Mirror Python 环境不存在，请先运行 setup-outlook-calendar.cmd。'
}

New-Item -ItemType Directory -Force -Path $logPath | Out-Null
try {
    $arguments = @('-m', 'time_mirror', 'sync-outlook')
    if ($Force) { $arguments += '--force' }
    $result = & $pythonPath @arguments 2>&1
    $exitCode = $LASTEXITCODE
    $result | Set-Content -LiteralPath $outputPath -Encoding UTF8
    $result | Write-Host
    if ($exitCode -ne 0) { throw ($result -join [Environment]::NewLine) }

    '' | Set-Content -LiteralPath $errorPath -Encoding UTF8
}
catch {
    ($_ | Out-String) | Set-Content -LiteralPath $errorPath -Encoding UTF8
    throw
}

