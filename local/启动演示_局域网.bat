@echo off
setlocal

set "SSH_ALIAS=workstation-vm201-remote"
set "LOCAL_PORT=18130"

echo ============================================
echo  传染病监测演示环境 一键启动（局域网共享版）
echo ============================================
echo.
echo 注意：此模式允许同一局域网内的其他设备访问演示页面，
echo       页面仍需登录账号密码。仅在可信网络中使用。
echo.

REM 1. 确保远程服务已启动
echo [1/4] 检查并启动远程服务（vm201）...
ssh -o BatchMode=yes -o ConnectTimeout=15 %SSH_ALIAS% "bash USER_HOME/projects/openclaw/runtime/ensure_demo_up.sh"
if errorlevel 1 (
    echo [错误] 无法连接或启动远程服务，请检查服务器与 SSH 配置。
    pause
    exit /b 1
)

REM 2. 若端口未通，则建立监听所有网卡的隧道（首次会弹出防火墙提示，请选择允许）
echo [2/4] 检查隧道端口 %LOCAL_PORT% ...
curl -s -o nul -m 3 http://127.0.0.1:%LOCAL_PORT%/readyz
if errorlevel 1 (
    echo       隧道不存在，正在建立（监听局域网）...
    start "demo-tunnel-18130" ssh -N -L 0.0.0.0:%LOCAL_PORT%:127.0.0.1:8020 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 %SSH_ALIAS%
) else (
    echo       端口已被占用。
    echo       若之前用的是本机版脚本，请先关闭 demo-tunnel-18130 窗口再运行本脚本。
)

REM 3. 等待服务就绪
echo [3/4] 等待服务就绪...
set /a tries=0
:wait_loop
curl -s -o nul -m 3 http://127.0.0.1:%LOCAL_PORT%/readyz
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

REM 4. 显示局域网访问地址并打开本机页面
echo [4/4] 本机局域网 IP（其他设备用下面的地址访问）：
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /R /C:"IPv4"') do (
    for /f "tokens=1" %%b in ("%%a") do echo       http://%%b:%LOCAL_PORT%/
)
echo.
start "" "http://127.0.0.1:%LOCAL_PORT%/"

echo 其他设备打开上面的地址即可，路径同理：
echo   可信数据演示    http://^<本机IP^>:%LOCAL_PORT%/
echo   预测知识图谱    http://^<本机IP^>:%LOCAL_PORT%/forecast-model-knowledge-graph
echo   预测结果页      http://^<本机IP^>:%LOCAL_PORT%/predictions
echo.
pause
endlocal
