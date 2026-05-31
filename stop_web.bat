@echo off
echo Stopping web dashboard...
powershell -Command "Get-Process -Name pythonw -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'app.py' } | Stop-Process -Force"
echo Done.
pause
