@echo off
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if errorlevel 1 (
  start "" /B python -m autoelearning.desktop_app
) else (
  start "" /B pythonw -m autoelearning.desktop_app
)
exit /b 0
