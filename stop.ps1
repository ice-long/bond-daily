$ErrorActionPreference = 'Stop'
$bondPidFile = Join-Path $PSScriptRoot 'data\server.pid'
if (-not (Test-Path -LiteralPath $bondPidFile)) { Write-Output 'No saved app process.'; exit }
$bondSavedPid = [int](Get-Content -LiteralPath $bondPidFile -Raw)
$bondProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $bondSavedPid" -ErrorAction SilentlyContinue
$bondExpectedPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if ($bondProcess) {
    if ($bondProcess.ExecutablePath -ne $bondExpectedPython -or $bondProcess.CommandLine -notmatch 'streamlit') {
        throw 'Saved PID does not belong to this app. No process was stopped.'
    }
    & taskkill.exe /PID $bondSavedPid /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the app process.' }
}
Remove-Item -LiteralPath $bondPidFile -Force
Write-Output 'Bond Daily stopped. Saved data remains available.'
