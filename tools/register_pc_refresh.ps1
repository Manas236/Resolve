param(
    [string]$Catalog,
    [ValidatePattern('^\d{2}:\d{2}$')][string]$At = '09:00'
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'Create the Resolve .venv before registering the task.' }
$arguments = '-m tools.pc_acquisition refresh --output state/pc_bundle.zip --send'
if ($Catalog) { $arguments += ' --catalog "' + (Resolve-Path $Catalog).Path + '"' }
$action = New-ScheduledTaskAction -Execute $python -WorkingDirectory $repo -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Sunday -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 8) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive
Register-ScheduledTask -TaskName 'Resolve weekly acquisition' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force
