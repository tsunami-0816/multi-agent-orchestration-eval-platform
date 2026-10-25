@echo off
set "PS_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS_EXE%" set "PS_EXE=%SystemRoot%\SysWOW64\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS_EXE%" (
    echo [ERROR] PowerShell not found!
    pause
    exit /b 1
)
"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%~dpn0.ps1"
