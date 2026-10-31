$ErrorActionPreference = 'Stop'

$runtimeRoot = Join-Path $env:LOCALAPPDATA 'TimeMirror'
$homePath = Join-Path $runtimeRoot 'syncthing'
$syncPath = Join-Path $runtimeRoot 'ActivityWatchSync'

function Find-SyncthingExecutable {
    $command = Get-Command syncthing.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }

    $packageRoot = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    $candidates = Get-ChildItem -LiteralPath $packageRoot -Filter syncthing.exe -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like '*Syncthing.Syncthing*' } |
        Sort-Object -Property LastWriteTimeUtc -Descending
    if ($candidates) { return $candidates[0].FullName }
    throw '未找到 Syncthing，请重新运行 setup-phone-sync.cmd。'
}

function Test-SyncthingReady {
    param(
        [string]$ConfigPath,
        [string]$ExpectedDeviceId
    )

    try {
        [xml]$configuration = Get-Content -LiteralPath $ConfigPath -Raw
        $headers = @{ 'X-API-Key' = [string]$configuration.configuration.gui.apikey }
        $status = Invoke-RestMethod -Headers $headers -TimeoutSec 1 http://127.0.0.1:8384/rest/system/status
        return $status.myID -eq $ExpectedDeviceId
    }
    catch {
        return $false
    }
}

New-Item -ItemType Directory -Force -Path $homePath, $syncPath | Out-Null
$syncthing = Find-SyncthingExecutable
$configPath = Join-Path $homePath 'config.xml'
$deviceId = & $syncthing --config $homePath --data $homePath device-id
if (Test-SyncthingReady -ConfigPath $configPath -ExpectedDeviceId $deviceId) { exit 0 }

& $syncthing --config $homePath --data $homePath serve --no-browser --no-restart --log-file=default

