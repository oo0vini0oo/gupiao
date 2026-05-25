$python = 'D:\Program Files\Python12\python.exe'
$script = 'D:\PycharmProjects\NewAnchorUITest\股票分析\main.py'
$workDir = 'D:\PycharmProjects\NewAnchorUITest\股票分析'

$action = New-ScheduledTaskAction -Execute $python -Argument $script -WorkingDirectory $workDir
$trigger = New-ScheduledTaskTrigger -Daily -At '15:05'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)

try {
    $task = Register-ScheduledTask -TaskName 'FetchStockData' -Action $action -Trigger $trigger -Settings $settings -Description 'Daily A-share cumulative data fetch (modular, stores to stock_daily table)' -User $env:USERNAME -RunLevel Limited -Force
    Write-Host "Scheduled task created/updated: FetchStockData"
    Write-Host "Trigger time: Weekdays 15:05 (5 min after market close)"
    Write-Host "Path: $python $script"
} catch {
    Write-Host "Error: $_"
    exit 1
}