@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python 3.10+ is required.
  pause
  exit /b 1
)

if not exist workspace mkdir workspace
set "DEVWB_HOME=%CD%\workspace"
set "DEVWB_HOST=127.0.0.1"
set "DEVWB_PORT=8642"

if not exist frontend\dist (
  echo [WARN] frontend\dist is missing. Run: npm install --prefix frontend ^&^& npm run build --prefix frontend
)

echo Starting Dev Workbench v2 at http://%DEVWB_HOST%:%DEVWB_PORT%/
echo DEVWB_HOME=%DEVWB_HOME%
python -m uvicorn server.app:app --host %DEVWB_HOST% --port %DEVWB_PORT%
set "EXIT_CODE=%ERRORLEVEL%"
echo Dev Workbench stopped with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
