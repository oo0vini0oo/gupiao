$python = 'D:\Program Files\Python12\pythonw.exe'
$script = 'D:\PycharmProjects\GuPiao\app.py'
$workDir = 'D:\PycharmProjects\GuPiao'

$action = New-ScheduledTaskAction -Execute $python -Argument $script -WorkingDirectory $workDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

try {
    $task = Register-ScheduledTask -TaskName 'StartWebDashboard' -Action $action -Trigger $trigger -Settings $settings -Description 'Start Flask web dashboard on user login (A-share stock analysis platform)' -RunLevel Limited -Force
    Write-Host "Scheduled task created/updated: StartWebDashboard"
    Write-Host "Trigger: At logon of $env:USERNAME"
    Write-Host "Path: $python $script"
} catch {
    Write-Host "Error: $_"
    exit 1
}
