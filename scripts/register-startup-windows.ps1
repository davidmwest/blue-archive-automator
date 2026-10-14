# Register (or remove) a Task Scheduler entry that starts the dashboard when you sign in.
#
#   pwsh scripts/register-startup-windows.ps1 -Port 8767 -OcrThreads 16 -Instance Pie64_4
#   pwsh scripts/register-startup-windows.ps1 -Remove
#
# The task runs scripts/serve-windows.ps1 -Foreground for the current user only,
# one minute after sign-in, without elevation. If the dashboard exits it is
# restarted up to ten times, a minute apart. It only helps after a reboot when
# Windows signs you in automatically; nothing runs at the lock screen.
param(
    [int]$Port = 8765,
    [ValidateRange(1, 64)][int]$OcrThreads = 2,
    [string]$Config = "config/local.toml",
    [string]$Instance = "",
    [string]$TaskName = "Maid in Schale Manager",
    [switch]$Remove
)

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    "Removed the '$TaskName' task"
    return
}

$root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot "serve-windows.ps1"
$pwsh = (Get-Command pwsh -ErrorAction Stop).Source
$user = "$env:USERDOMAIN\$env:USERNAME"
$arguments = "-NoProfile -WindowStyle Hidden -File `"$script`" -Foreground -Port $Port -OcrThreads $OcrThreads -Config `"$Config`""
if ($Instance) {
    if ($Instance -notmatch '^[A-Za-z0-9_]+$') {
        throw "Instance must be a BlueStacks internal name such as Pie64_4"
    }
    $arguments += " -Instance $Instance"
}

$action = New-ScheduledTaskAction -Execute $pwsh -Argument $arguments -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = "PT1M"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force `
    -Description "Starts the Maid in Schale dashboard (Blue Archive automator) at sign-in." | Out-Null
"Registered '$TaskName' for ${user}: dashboard on port $Port at sign-in"
