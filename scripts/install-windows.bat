@echo off
REM Windows Installation Script for OT Lab Local Runtime
REM This script removes Windows Defender SmartScreen warning and sets permissions

setlocal enabledelayedexpansion

set AGENT_NAME=otlab-agent-windows-amd64.exe
set SCRIPT_DIR=%~dp0
set AGENT_PATH=%SCRIPT_DIR%%AGENT_NAME%

cls
echo ==================================================
echo   OT Lab Local Runtime - Windows Installation
echo ==================================================
echo.

REM Check if agent exists
if not exist "%AGENT_PATH%" (
    echo Error: Runtime binary not found at %AGENT_PATH%
    echo.
    echo Please download the Local Runtime first from the OT Lab dashboard:
    echo   Open the OT Lab App dashboard and use the Download Runtime button
    pause
    exit /b 1
)

echo + Found Local Runtime at: %AGENT_PATH%
echo.

REM Step 1: Remove Zone.Identifier (SmartScreen warning)
echo 1/4 Step 1: Removing Windows SmartScreen flag...
powershell.exe -NoProfile -Command "Remove-Item -Path '%AGENT_PATH%:Zone.Identifier' -Force -ErrorAction SilentlyContinue"
if %ERRORLEVEL% equ 0 (
    echo    + SmartScreen flag removed
) else (
    echo    ! Could not remove flag (may not exist or require admin)
)
echo.

REM Step 2: Set file attributes
echo 1/4 Step 2: Setting file attributes...
attrib -H "%AGENT_PATH%" --gui
echo    + Attributes set
echo.

REM Step 3: Verify execution
echo 1/4 Step 3: Verifying installation...
if exist "%AGENT_PATH%" (
    echo    + Local Runtime is ready to use
) else (
    echo    Error: Agent file not found
    pause
    exit /b 1
)
echo.

REM Step 4: Check Npcap
echo 1/4 Step 4: Checking Npcap (packet capture driver)...
reg query "HKLM\SYSTEM\CurrentControlSet\Services\npcap" >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo    + Npcap is installed
) else (
    echo    ! Npcap not found
    echo    ! The agent requires Npcap for packet capture
    echo    ! Download from: https://nmap.org/npcap/
)
echo.

echo ==================================================
echo   Installation Complete! 
echo ==================================================
echo.
echo To run the Local Runtime, double-click:
echo   %AGENT_PATH%
echo.
echo Or open Command Prompt and type:
echo   "%AGENT_PATH%" --gui
echo.
echo For more information, visit:
echo   Open the OT Lab App dashboard
echo.
echo Starting Local Runtime UI now...
"%AGENT_PATH%" --gui
