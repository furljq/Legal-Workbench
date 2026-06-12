@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

set "PORT=8787"
if not "%LEGAL_WORKBENCH_PORT%"=="" set "PORT=%LEGAL_WORKBENCH_PORT%"

set "HOST=127.0.0.1"
if not "%LEGAL_WORKBENCH_HOST%"=="" set "HOST=%LEGAL_WORKBENCH_HOST%"

set "ROOT=%CD%"
set "VENV=%ROOT%\.venv"
set "URL=http://%HOST%:%PORT%/"

echo [LegalWorkbench] Starting...
echo [LegalWorkbench] Folder: %ROOT%
echo.

call :find_python
if not "%PY_FOUND%"=="1" (
  echo [LegalWorkbench] Python 3.10 or later was not found.
  echo Please install Python from https://www.python.org/downloads/windows/
  echo Then run start_windows.bat again.
  echo.
  pause
  exit /b 1
)

echo [LegalWorkbench] Using Python: %PY_EXE% %PY_ARGS%

if not exist "%VENV%\Scripts\python.exe" (
  echo [LegalWorkbench] Creating local virtual environment...
  "%PY_EXE%" %PY_ARGS% -m venv "%VENV%"
  if errorlevel 1 (
    echo [LegalWorkbench] Failed to create .venv.
    pause
    exit /b 1
  )
)

echo [LegalWorkbench] Installing dependencies...
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
"%VENV%\Scripts\python.exe" -m pip install -r "%ROOT%\requirements.txt"
if errorlevel 1 (
  echo [LegalWorkbench] Dependency installation failed.
  pause
  exit /b 1
)

echo [LegalWorkbench] Checking port %PORT%...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
  if not "%%P"=="" (
    echo [LegalWorkbench] Port %PORT% is occupied by PID %%P. Closing it...
    taskkill /PID %%P /F >nul 2>nul
  )
)

echo.
echo [LegalWorkbench] Starting service: %URL%
echo [LegalWorkbench] Keep this window open while using the workbench.
echo.
"%VENV%\Scripts\python.exe" "%ROOT%\app\server.py" --host "%HOST%" --port "%PORT%" --open-browser

set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo [LegalWorkbench] Service stopped.
pause
exit /b %EXIT_CODE%

:find_python
set "PY_FOUND=0"
set "PY_EXE="
set "PY_ARGS="
call :try_python "py" "-3"
if "%PY_FOUND%"=="1" exit /b 0
call :try_python "python" ""
if "%PY_FOUND%"=="1" exit /b 0
exit /b 0

:try_python
if "%~1"=="" exit /b 0
"%~1" %~2 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if not errorlevel 1 (
  set "PY_EXE=%~1"
  set "PY_ARGS=%~2"
  set "PY_FOUND=1"
)
exit /b 0

