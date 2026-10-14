# Start the dashboard on Windows, with logs under data/logs.
#
#   pwsh scripts/serve-windows.ps1 -Port 8767 -OcrThreads 16
#
# By default the dashboard starts as a detached process and this script returns.
# -Foreground keeps it in this process instead, which is what a logon task needs
# so Task Scheduler can see it running and restart it; see
# scripts/register-startup-windows.ps1. -Instance names a BlueStacks 5 instance
# (for example Pie64_4) to start first when it is not already running. Jobs may
# then also restart that one instance if it becomes unreachable or its Android
# storage turns read-only.
#
# Python runs in UTF-8 mode so a non-UTF-8 system code page cannot affect file I/O.
# Raise -OcrThreads when tasks reach a screen and then report that they did not:
# a frame is only actionable for a few seconds, and slower cores need more threads
# to read a native-resolution screenshot inside that window.
param(
    [int]$Port = 8765,
    [ValidateRange(1, 64)][int]$OcrThreads = 2,
    [string]$Config = "config/local.toml",
    [string]$Instance = "",
    [string]$BlueStacks = "C:\Program Files\BlueStacks_nxt",
    [switch]$Foreground
)

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Create the virtual environment first: py -3.11 -m venv .venv"
}
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use; choose another with -Port"
}

if ($Instance) {
    if ($Instance -notmatch '^[A-Za-z0-9_]+$') {
        throw "Instance must be a BlueStacks internal name such as Pie64_4"
    }
    $running = & (Join-Path $BlueStacks "BstkVMMgr.exe") list runningvms 2>$null |
        Select-String -SimpleMatch "`"$Instance`""
    if (-not $running) {
        Start-Process -FilePath (Join-Path $BlueStacks "HD-Player.exe") -ArgumentList "--instance", $Instance
    }
}

$logs = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force $logs | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$out = Join-Path $logs "serve-$stamp.out.log"
$err = Join-Path $logs "serve-$stamp.err.log"
$env:PYTHONUTF8 = "1"
$env:BA_OCR_THREADS = "$OcrThreads"
if ($Instance) {
    # Lets a job restart this one instance when it is unreachable or read-only.
    $env:BA_BLUESTACKS_INSTANCE = $Instance
    $env:BA_BLUESTACKS_DIR = $BlueStacks
}
$arguments = "-X", "utf8", "-m", "ba_automator", "--config", $Config, "serve", "--port", $Port

if ($Foreground) {
    Set-Location $root
    & $python @arguments 1>> $out 2>> $err
    exit $LASTEXITCODE
}
$process = Start-Process -FilePath $python -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -ArgumentList $arguments -RedirectStandardOutput $out -RedirectStandardError $err
"Dashboard starting at http://127.0.0.1:$Port (pid $($process.Id)); logs in $logs"
