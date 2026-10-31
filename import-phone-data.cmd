@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0import-phone-data.ps1" %*
pause

