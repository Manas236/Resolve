param(
    [Parameter(Mandatory=$true)][string]$Catalog,
    [ValidatePattern('^\d{2}:\d{2}$')][string]$At = '09:00'
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$catalogPath = (Resolve-Path $Catalog).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'Create the Resolve .venv before registering the task.' }
$action = New-ScheduledTaskAction -Execute $python -WorkingDirectory $repo -Argument "-m tools.pc_acquisition refresh --catalog `"$catalogPath`" --output state/pc_bundle.zip --send"
$trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Sunday -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 8) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive
Register-ScheduledTask -TaskName 'Resolve weekly acquisition' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force
