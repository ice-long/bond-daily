param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $PSScriptRoot
$bondPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $bondPython)) { & (Join-Path $PSScriptRoot 'setup.ps1') }
$bondData = Join-Path $PSScriptRoot 'data'
New-Item -ItemType Directory -Path $bondData -Force | Out-Null
$bondUrl = 'http://127.0.0.1:8851'
$bondPidFile = Join-Path $bondData 'server.pid'
$bondAlreadyRunning = $false
if (Test-Path -LiteralPath $bondPidFile) {
    $bondSavedPid = [int](Get-Content -LiteralPath $bondPidFile -Raw)
    $bondExisting = Get-CimInstance Win32_Process -Filter "ProcessId = $bondSavedPid" -ErrorAction SilentlyContinue
    if ($bondExisting -and $bondExisting.ExecutablePath -eq $bondPython -and $bondExisting.CommandLine -match 'streamlit') {
        $bondAlreadyRunning = $true
    }
}
if (-not $bondAlreadyRunning) {
    $bondPort = Get-NetTCPConnection -LocalPort 8851 -State Listen -ErrorAction SilentlyContinue
    if ($bondPort) { throw 'Port 8851 is in use by another process. Close it before starting this app.' }
    $bondProcess = Start-Process -FilePath $bondPython -WorkingDirectory $PSScriptRoot -ArgumentList @('-m','streamlit','run','app.py') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $bondData 'server.stdout.log') -RedirectStandardError (Join-Path $bondData 'server.stderr.log')
    Set-Content -LiteralPath $bondPidFile -Value $bondProcess.Id -Encoding ascii
}
$bondReady = $false
for ($bondAttempt = 0; $bondAttempt -lt 60; $bondAttempt++) {
    try {
        $bondHealth = Invoke-WebRequest -UseBasicParsing -Uri "$bondUrl/_stcore/health" -TimeoutSec 1
        if ($bondHealth.StatusCode -eq 200) { $bondReady = $true; break }
    } catch { Start-Sleep -Milliseconds 250 }
}
if (-not $bondReady) { throw 'App did not start. Inspect data\server.stderr.log.' }
Write-Output "Bond Daily is running at $bondUrl"
if (-not $NoBrowser) { Start-Process $bondUrl }
