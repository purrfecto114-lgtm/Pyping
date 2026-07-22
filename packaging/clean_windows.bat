@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%CD%\packaging\build_windows.ps1" -Mode clean %*
exit /b %ERRORLEVEL%
