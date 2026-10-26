@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Dev Workbench
cd /d "%~dp0"

echo ==============================================
echo   开发工作台 · Dev Workbench 启动脚本
echo ==============================================

where python >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 python，请先安装 Python 3.10+ 并加入 PATH。
  pause
  exit /b 1
)

if not exist .venv (
  echo [1/4] 创建 Python 虚拟环境...
  python -m venv .venv
  if errorlevel 1 goto :fail
)

echo [2/4] 检查后端依赖...
.venv\Scripts\python -c "import fastapi, uvicorn, httpx, pydantic" >nul 2>&1
if errorlevel 1 (
  echo        安装后端依赖（首次较慢）...
  .venv\Scripts\python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple fastapi "uvicorn[standard]" httpx pydantic python-multipart
  if errorlevel 1 goto :fail
)

echo [3/4] 检查前端构建产物...
if not exist frontend\dist\index.html (
  echo        未发现 dist，构建前端（需要 Node.js，首次较慢）...
  pushd frontend
  if not exist node_modules (
    call npm install --no-audit --no-fund
    if errorlevel 1 (popd & goto :fail)
  )
  call npm run build
  if errorlevel 1 (popd & goto :fail)
  popd
)

echo [4/4] 启动服务（仅本机 127.0.0.1:8642）...
echo.
echo   主页:     http://127.0.0.1:8642/
echo   API 文档: http://127.0.0.1:8642/api/docs
echo   停止服务: 本窗口按 Ctrl+C，或直接关闭窗口
echo.

if not defined DEVWB_NO_BROWSER (
  start "" /min cmd /c "timeout /t 4 /nobreak >nul & start http://127.0.0.1:8642/"
)

.venv\Scripts\python -m uvicorn server.app:app --host 127.0.0.1 --port 8642
echo.
echo 服务已停止。
pause
exit /b 0

:fail
echo.
echo [错误] 启动失败，请查看上方错误信息。
pause
exit /b 1
