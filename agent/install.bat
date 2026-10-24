@echo off
setlocal
rem ZhizPrintAgent per-user install (no admin required, plan 2).
rem Expected next to this script: agent.exe, SumatraPDF.exe (3.6.1 x64),
rem SumatraPDF-settings.txt.
set AGENT_DIR=%LOCALAPPDATA%\ZhizPrintAgent
if not exist "%AGENT_DIR%" mkdir "%AGENT_DIR%"
copy /y "%~dp0agent.exe" "%AGENT_DIR%\agent.exe" >nul
if errorlevel 1 goto :fail
if exist "%~dp0SumatraPDF.exe" copy /y "%~dp0SumatraPDF.exe" "%AGENT_DIR%\SumatraPDF.exe" >nul
if exist "%~dp0SumatraPDF-settings.txt" copy /y "%~dp0SumatraPDF-settings.txt" "%AGENT_DIR%\SumatraPDF-settings.txt" >nul
schtasks /create /f /sc onlogon /rl limited /tn "ZhizPrintAgent" /tr "\"%AGENT_DIR%\agent.exe\" run"
if errorlevel 1 goto :fail
"%AGENT_DIR%\agent.exe" install
echo.
echo Install done. Next steps:
echo   1. Run: "%AGENT_DIR%\agent.exe" doctor
echo   2. The kiosk scan page opens automatically at logon.
exit /b 0
:fail
echo.
echo Install FAILED - see the messages above.
exit /b 1
