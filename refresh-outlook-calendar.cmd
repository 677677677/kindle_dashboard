@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync-outlook-calendar.ps1" -Force
pause

