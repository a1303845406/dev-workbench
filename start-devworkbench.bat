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
set "DEVWB_URL=http://%DEVWB_HOST%:%DEVWB_PORT%/"

if not exist apps\web\dist (
  echo [WARN] apps\web\dist is missing. Run: npm install --prefix apps\web ^&^& npm run build --prefix apps\web
)

echo Starting Dev Workbench v2 at http://%DEVWB_HOST%:%DEVWB_PORT%/
echo DEVWB_HOME=%DEVWB_HOME%

rem Poll /api/health in the background; open the default browser once the service is up.
start "devwb-browser" /min powershell -NoProfile -Command "for($i=0;$i -lt 60;$i++){try{Invoke-RestMethod -Uri '%DEVWB_URL%api/health' -TimeoutSec 1 -ErrorAction Stop|Out-Null;break}catch{};Start-Sleep -Milliseconds 500};Start-Process '%DEVWB_URL%'"

python -m uvicorn devworkbench.main:app --app-dir apps/api/src --host %DEVWB_HOST% --port %DEVWB_PORT%
set "EXIT_CODE=%ERRORLEVEL%"
echo Dev Workbench stopped with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%