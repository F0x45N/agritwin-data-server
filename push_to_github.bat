@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0push_to_github.ps1" -RemoteUrl "%~1"
