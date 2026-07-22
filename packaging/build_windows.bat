@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%CD%\packaging\build_windows.ps1" -Mode release %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Build failed with exit code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%
