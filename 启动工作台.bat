@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 开发工作台 · Dev Workbench

echo ==============================================
echo   开发工作台 · Dev Workbench 启动脚本
echo ==============================================

if not exist .venv (
  echo [1/4] 创建 Python 虚拟环境...
  python -m venv .venv || goto :err
)

echo [2/4] 安装后端依赖（如已安装会跳过）...
.venv\Scripts\python -c "import fastapi, uvicorn, httpx, pydantic" 2>nul || (
  .venv\Scripts\python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple fastapi "uvicorn[standard]" httpx pydantic python-multipart || goto :err
)

echo [3/4] 检查前端构建产物...
if not exist frontend\dist\index.html (
  echo        未发现 dist，开始构建前端（需要 Node.js）...
  pushd frontend
  if not exist node_modules (
    call npm install --no-audit --no-fund
    if errorlevel 1 (popd & goto :err)
  )
  call npm run build
  if errorlevel 1 (popd & goto :err)
  popd
)

echo [4/4] 启动服务（仅本机 127.0.0.1:8642）...
echo.
echo   浏览器访问: http://127.0.0.1:8642/
echo   API 文档:   http://127.0.0.1:8642/api/docs
echo.
start "" /min cmd /c "timeout /t 4 /nobreak >nul && start "" http://127.0.0.1:8642/"
.venv\Scripts\python -m uvicorn server.app:app --host 127.0.0.1 --port 8642
goto :eof

:err
echo.
echo 启动失败，请检查上方错误信息。
pause
