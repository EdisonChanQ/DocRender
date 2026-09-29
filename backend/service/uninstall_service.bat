@echo off
setlocal
set "SERVICE_NAME=FileTextParser"
set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" "%~dp0windows_service.py" stop
"%PYTHON%" "%~dp0windows_service.py" remove

echo [OK] %SERVICE_NAME% removed.
