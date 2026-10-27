@echo off
chcp 65001 >nul
REM Ecom Profile 项目一键启动脚本
REM 顺序：智能体(:2024) -> 等待就绪 -> 后台管理(:5000) + 侧边栏(:8000)
REM 后台管理依赖智能体 token，必须等智能体先起来

set ROOT=%~dp0
set PY=%ROOT%.venv\Scripts\python.exe
set LG=%ROOT%.venv\Scripts\langgraph.exe

echo [1/3] 启动智能体服务 :2024 ...
start "ecom-agent-2024" cmd /k "cd /d %ROOT%ecom_agent && %LG% dev --port 2024 --no-browser --no-reload"

echo 等待智能体就绪（约 20 秒）...
timeout /t 20 /nobreak >nul

echo [2/3] 启动后台管理服务 :5000 ...
start "ecom-admin-5000" cmd /k "cd /d %ROOT%ecom_admin && %PY% ecom_admin_app.py"

echo [3/3] 启动侧边栏服务 :8000 ...
start "ecom-sidebar-8000" cmd /k "cd /d %ROOT%ecom_sidebar && %PY% -m uvicorn ecom_sidebar_app:app --host 127.0.0.1 --port 8000"

echo.
echo 全部启动命令已发出，各服务运行在独立窗口：
echo   智能体   http://127.0.0.1:2024/docs
echo   后台管理 http://127.0.0.1:5000/login
echo   侧边栏   http://127.0.0.1:8000/ecom_web/login.html
echo   （侧边栏 WEB 登录账号：THOR，密码见 ecom_sidebar\.env 的 WEB_PASSWORD）
echo.
echo 关闭对应 cmd 窗口即可停止对应服务。
pause
