param(
    [Parameter(Position = 0)]
    [string]$SourcePath
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'TimeMirror'
$importDirectory = Join-Path $runtimeRoot 'imports'
$destination = Join-Path $importDirectory 'activitywatch-android.json'
$temporary = "$destination.importing"

function Select-AndroidExport {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = '选择 ActivityWatch Android 导出文件'
    $dialog.Filter = 'ActivityWatch JSON (*.json)|*.json|所有文件 (*.*)|*.*'
    $dialog.Multiselect = $false
    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
        return $null
    }
    return $dialog.FileName
}

if (-not $SourcePath) {
    $SourcePath = Select-AndroidExport
}
if (-not $SourcePath) {
    Write-Host '已取消，没有修改手机数据。'
    exit 0
}

$source = Get-Item -LiteralPath $SourcePath
$pythonReady = Test-Path -LiteralPath $pythonPath
if (-not $pythonReady) {
    throw '首次使用请先运行 start-dashboard.cmd，再导入手机数据。'
}
$document = Get-Content -LiteralPath $source.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
$bucket = $document.buckets.'aw-watcher-android'
if (-not $bucket -or $bucket.type -ne 'currentwindow' -or
    -not ($bucket.PSObject.Properties.Name -contains 'events')) {
    throw '所选文件不是 aw-watcher-android 的 App 使用记录导出。'
}

New-Item -ItemType Directory -Force -Path $importDirectory | Out-Null
try {
    Copy-Item -LiteralPath $source.FullName -Destination $temporary -Force
    & $pythonPath -m time_mirror validate-phone $temporary | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Android 导出内容校验失败，原有手机数据未被替换。'
    }
    Move-Item -LiteralPath $temporary -Destination $destination -Force
}
finally {
    if (Test-Path -LiteralPath $temporary) {
        Remove-Item -LiteralPath $temporary -Force
    }
}

Write-Host "手机数据已导入：$destination"
& $pythonPath -m time_mirror refresh | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw '手机数据已保存，但 Dashboard 快照刷新失败。请重新启动 Dashboard。'
}
Write-Host 'Dashboard 快照已刷新。'

