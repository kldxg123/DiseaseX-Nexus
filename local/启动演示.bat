@echo off
setlocal

set "SSH_ALIAS=workstation-vm201-remote"
set "LOCAL_PORT=18130"
set "BASE=http://127.0.0.1:%LOCAL_PORT%"

echo ============================================
echo  传染病监测演示环境 一键启动
echo ============================================

REM 1. 确保远程服务已启动（vm201 上幂等脚本：PostgreSQL + Dashboard）
echo [1/4] 检查并启动远程服务（vm201）...
ssh -o BatchMode=yes -o ConnectTimeout=15 %SSH_ALIAS% "bash USER_HOME/projects/openclaw/runtime/ensure_demo_up.sh"
if errorlevel 1 (
    echo [错误] 无法连接或启动远程服务，请检查服务器与 SSH 配置。
    pause
    exit /b 1
)

REM 2. 若本地隧道端口未通，则后台新建 SSH 隧道窗口
echo [2/4] 检查本地隧道 127.0.0.1:%LOCAL_PORT% ...
curl -s -o nul -m 3 %BASE%/readyz
if errorlevel 1 (
    echo       隧道不存在，正在建立...
    start "demo-tunnel-18130" ssh -N -L %LOCAL_PORT%:127.0.0.1:8020 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 %SSH_ALIAS%
) else (
    echo       隧道已存在，跳过。
)

REM 3. 等待服务就绪（最多 60 秒）
echo [3/4] 等待服务就绪...
set /a tries=0
:wait_loop
curl -s -o nul -m 3 %BASE%/readyz
if not errorlevel 1 goto ready
set /a tries+=1
if %tries% geq 20 (
    echo [错误] 等待超时，服务未就绪。
    pause
    exit /b 1
)
timeout /t 3 /nobreak >nul
goto wait_loop
:ready
echo       服务已就绪。

REM 4. 打开三个演示页面
echo [4/4] 打开演示页面（首次需登录）...
start "" "%BASE%/"
start "" "%BASE%/forecast-model-knowledge-graph"
start "" "%BASE%/predictions"

echo.
echo 完成。演示地址：%BASE%/
echo 关闭演示：关闭名为 demo-tunnel-18130 的窗口即可断开隧道。
endlocal
