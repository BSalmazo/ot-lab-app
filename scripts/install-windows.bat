@echo off
REM OT Lab Local Runtime - Windows setup & launch

setlocal enabledelayedexpansion

set AGENT_NAME=otlab-agent-windows-amd64.exe
set SCRIPT_DIR=%~dp0
set AGENT_PATH=%SCRIPT_DIR%%AGENT_NAME%
set VERBOSE=0

for %%A in (%*) do (
    if /I "%%A"=="-v"       set VERBOSE=1
    if /I "%%A"=="--verbose" set VERBOSE=1
    if /I "%%A"=="/v"       set VERBOSE=1
)

cls

if "%VERBOSE%"=="1" (
    echo ==================================================
    echo   OT Lab Local Runtime - Windows Setup ^& Launch
    echo ==================================================
    echo.
) else (
    echo.
    echo   OT Lab Local Runtime
)

if not exist "%AGENT_PATH%" (
    echo Error: Runtime binary not found at %AGENT_PATH%
    echo   Download it from the OT Lab dashboard.
    pause
    exit /b 1
)

if "%VERBOSE%"=="1" (
    echo Removing Windows SmartScreen flag...
)
powershell.exe -NoProfile -Command "Remove-Item -Path '%AGENT_PATH%:Zone.Identifier' -Force -ErrorAction SilentlyContinue" >nul 2>&1
attrib -H "%AGENT_PATH%" >nul 2>&1

if "%VERBOSE%"=="1" (
    echo    + SmartScreen flag removed
    echo.
    echo Checking Npcap ^(packet capture driver^)...
    reg query "HKLM\SYSTEM\CurrentControlSet\Services\npcap" >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo    + Npcap is installed
    ) else (
        echo    ! Npcap not found - required for packet capture
        echo    ! Download from: https://nmap.org/npcap/
    )
    echo.
)

echo   + Runtime ready
echo.
echo   Starting Local Runtime UI...
echo.
"%AGENT_PATH%" --gui
