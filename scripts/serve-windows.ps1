# Start the dashboard on Windows as a detached process, with logs under data/logs.
#
#   pwsh scripts/serve-windows.ps1 -Port 8767 -OcrThreads 16
#
# Python runs in UTF-8 mode so a non-UTF-8 system code page cannot affect file I/O.
# Raise -OcrThreads when tasks reach a screen and then report that they did not:
# a frame is only actionable for a few seconds, and slower cores need more threads
# to read a native-resolution screenshot inside that window.
param(
    [int]$Port = 8765,
    [ValidateRange(1, 64)][int]$OcrThreads = 2,
    [string]$Config = "config/local.toml"
)

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Create the virtual environment first: py -3.11 -m venv .venv"
}
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use; choose another with -Port"
}

$logs = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force $logs | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$env:PYTHONUTF8 = "1"
$env:BA_OCR_THREADS = "$OcrThreads"
$process = Start-Process -FilePath $python -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -ArgumentList "-X", "utf8", "-m", "ba_automator", "--config", $Config, "serve", "--port", $Port `
    -RedirectStandardOutput (Join-Path $logs "serve-$stamp.out.log") `
    -RedirectStandardError (Join-Path $logs "serve-$stamp.err.log")
"Dashboard starting at http://127.0.0.1:$Port (pid $($process.Id)); logs in $logs"
