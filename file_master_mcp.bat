@echo off
setlocal enabledelayedexpansion

:: File Master MCP Management Script for Windows
:: This script provides commands to start, stop, restart, and check the status of the File Master MCP

:: Configuration
set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%venv"
set "SERVER_SCRIPT=%SCRIPT_DIR%src\file_master_mcp_server.py"
set "PID_FILE=%SCRIPT_DIR%mcp_server.pid"
set "LOG_FILE=%SCRIPT_DIR%mcp_server.log"
set "SERVER_NAME=File Master MCP"
set "PORT=6466"

:: ANSI color codes for Windows 10+ (if supported)
set "GREEN=[32m"
set "RED=[31m"
set "YELLOW=[33m"
set "BLUE=[34m"
set "RESET=[0m"

:: Check if ANSI colors are supported
reg query "HKEY_CURRENT_USER\Console" /v VirtualTerminalLevel >nul 2>&1
if %ERRORLEVEL% neq 0 (
    set "GREEN="
    set "RED="
    set "YELLOW="
    set "BLUE="
    set "RESET="
)

:: Function to check Python installation
:check_python
python --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "PYTHON_CMD=python"
    exit /b 0
)
python3 --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "PYTHON_CMD=python3"
    exit /b 0
)
echo %RED%Python not found. Please install Python 3.x%RESET%
exit /b 1

:: Function to setup virtual environment
:setup_venv
if not exist "%VENV_DIR%" (
    echo %YELLOW%Setting up virtual environment...%RESET%
    call :check_python
    if !ERRORLEVEL! neq 0 exit /b 1

    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if !ERRORLEVEL! neq 0 (
        echo %RED%Failed to create virtual environment%RESET%
        exit /b 1
    )

    call "%VENV_DIR%\Scripts\activate.bat"
    pip install -r "%SCRIPT_DIR%requirements.txt"
    if !ERRORLEVEL! neq 0 (
        echo %RED%Failed to install requirements%RESET%
        exit /b 1
    )
    echo %GREEN%Virtual environment setup complete%RESET%
)
exit /b 0

:: Function to activate virtual environment
:activate_venv
if not exist "%VENV_DIR%" (
    call :setup_venv
    if !ERRORLEVEL! neq 0 exit /b 1
)

if exist "%VENV_DIR%\Scripts\activate.bat" (
    call "%VENV_DIR%\Scripts\activate.bat"
) else (
    echo %RED%Could not find virtual environment activation script%RESET%
    exit /b 1
)
exit /b 0

:: Function to kill process tree
:kill_process_tree
set "PID=%~1"
for /f "tokens=2" %%a in ('tasklist /fi "PPID eq %PID%" /fo list ^| find "PID:"') do (
    call :kill_process_tree %%a
)
taskkill /F /PID %PID% >nul 2>&1
exit /b 0

:: Function to check if port is in use
:check_port
netstat -ano | findstr ":%PORT%" >nul
if %ERRORLEVEL% equ 0 (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%"') do (
        set "PORT_PID=%%a"
    )
    exit /b 0
)
exit /b 1

:: Function to stop the server
:stop_server
echo %BLUE%Stopping %SERVER_NAME%...%RESET%
set "STOPPED=0"

:: Try to stop using PID file
if exist "%PID_FILE%" (
    set /p PID=<"%PID_FILE%"
    tasklist /FI "PID eq !PID!" 2>nul | find "!PID!" >nul
    if !ERRORLEVEL! equ 0 (
        echo %YELLOW%Stopping process tree for PID !PID!...%RESET%
        call :kill_process_tree !PID!
        set "STOPPED=1"
    )
    del "%PID_FILE%" 2>nul
)

:: Check for processes using our port
call :check_port
if !ERRORLEVEL! equ 0 (
    echo %YELLOW%Stopping process using port %PORT% (PID: !PORT_PID!)...%RESET%
    call :kill_process_tree !PORT_PID!
    set "STOPPED=1"
)

:: Clean up any Python processes running our script
for /f "tokens=2" %%a in ('tasklist /v ^| findstr /i "%SERVER_SCRIPT%"') do (
    echo %YELLOW%Cleaning up Python process (PID: %%a)...%RESET%
    call :kill_process_tree %%a
    set "STOPPED=1"
)

:: Clean up any remaining venv Python processes
if exist "%VENV_DIR%" (
    for /f "tokens=2" %%a in ('tasklist /v ^| findstr /i "%VENV_DIR%\Scripts\python.exe"') do (
        echo %YELLOW%Cleaning up venv Python process (PID: %%a)...%RESET%
        call :kill_process_tree %%a
        set "STOPPED=1"
    )
)

if !STOPPED! equ 1 (
    echo %GREEN%Server stopped successfully%RESET%
    exit /b 0
) else (
    echo %YELLOW%No running server processes found%RESET%
    exit /b 0
)

:: Function to start the server
:start_server
echo %BLUE%Starting %SERVER_NAME%...%RESET%

:: Stop any existing instances first
call :stop_server

:: Setup and activate virtual environment
call :activate_venv
if !ERRORLEVEL! neq 0 exit /b 1

:: Check if port is already in use
call :check_port
if !ERRORLEVEL! equ 0 (
    echo %RED%Port %PORT% is still in use. Cannot start server.%RESET%
    exit /b 1
)

:: Start the server using venv Python
echo %GREEN%Starting MCP server...%RESET%
start "Log MCP Server" /min cmd /c ""%VENV_DIR%\Scripts\python.exe" "%SERVER_SCRIPT%" > "%LOG_FILE%" 2>&1"
if !ERRORLEVEL! neq 0 (
    echo %RED%Failed to start the server%RESET%
    exit /b 1
)

:: Wait for the server to start and get its PID
timeout /t 2 /nobreak >nul
for /f "tokens=2" %%a in ('tasklist /fi "WINDOWTITLE eq Log MCP Server" /fo list ^| find "PID:"') do (
    set "PID=%%a"
    echo !PID!> "%PID_FILE%"
    echo %GREEN%Server started successfully with PID !PID!%RESET%
    exit /b 0
)

echo %RED%Failed to get server PID%RESET%
exit /b 1

:: Function to restart the server
:restart_server
echo %BLUE%Restarting %SERVER_NAME%...%RESET%
call :stop_server
timeout /t 2 /nobreak >nul
call :start_server
exit /b %ERRORLEVEL%

:: Function to check server status
:status_server
echo %BLUE%Checking %SERVER_NAME% status...%RESET%
set "SERVER_RUNNING=0"
set "PORT_IN_USE=0"

if exist "%PID_FILE%" (
    set /p PID=<"%PID_FILE%"
    tasklist /FI "PID eq !PID!" 2>nul | find "!PID!" >nul
    if !ERRORLEVEL! equ 0 (
        echo %GREEN%Server process is running (PID: !PID!)%RESET%
        set "SERVER_RUNNING=1"
    ) else (
        echo %RED%Server process is not running%RESET%
        del "%PID_FILE%" 2>nul
    )
) else (
    echo %RED%Server is not running (no PID file)%RESET%
)

call :check_port
if !ERRORLEVEL! equ 0 (
    echo %YELLOW%Port %PORT% is in use by process !PORT_PID!%RESET%
    set "PORT_IN_USE=1"
) else (
    echo %GREEN%Port %PORT% is free%RESET%
)

if !SERVER_RUNNING! equ 1 if !PORT_IN_USE! equ 1 (
    echo %GREEN%Server is fully operational%RESET%
) else if !SERVER_RUNNING! equ 0 if !PORT_IN_USE! equ 1 (
    echo %RED%Warning: Port is in use but server process is not running%RESET%
)

if exist "%LOG_FILE%" (
    echo.
    echo Last few lines of log file:
    type "%LOG_FILE%" | more
)
exit /b 0

:: Main script logic
if "%1"=="" goto usage
if "%1"=="start" goto start_server
if "%1"=="stop" goto stop_server
if "%1"=="restart" goto restart_server
if "%1"=="status" goto status_server
if "%1"=="help" goto usage

:usage
echo %BLUE%%SERVER_NAME% Management Script%RESET%
echo.
echo Usage: %0 [command]
echo.
echo Commands:
echo   start   - Start the server
echo   stop    - Stop the server
echo   restart - Restart the server
echo   status  - Check server status
echo   help    - Show this help message
echo.
exit /b 0 