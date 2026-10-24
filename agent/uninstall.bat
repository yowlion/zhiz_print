@echo off
setlocal
schtasks /delete /f /tn "ZhizPrintAgent" >nul 2>&1
taskkill /f /im agent.exe >nul 2>&1
echo ZhizPrintAgent autostart removed and agent stopped.
echo Leftovers you can delete manually:
echo   %LOCALAPPDATA%\ZhizPrintAgent
exit /b 0
