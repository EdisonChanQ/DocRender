@echo off
setlocal
set "SERVICE_NAME=FileTextParser"
set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" "%~dp0windows_service.py" --startup auto install
if errorlevel 1 goto :error
"%PYTHON%" "%~dp0windows_service.py" start
if errorlevel 1 goto :error

echo [OK] %SERVICE_NAME% installed and started.
goto :eof

:error
echo [FAILED] Could not install %SERVICE_NAME%. Run as Administrator.
exit /b 1
