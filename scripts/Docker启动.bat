@echo off
chcp 65001 >nul
title Docker 一键启动

echo ============================================================
echo            多智能体平台 - Docker 一键启动
echo ============================================================
echo.
echo 本方式需要已安装 Docker Desktop。
echo 如果你没有 Docker，建议使用 "一键启动.bat"（普通方式）。
echo.

cd /d "%~dp0\.."

REM 检查 Docker
docker --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Docker！
    echo.
    echo 请先安装 Docker Desktop：https://www.docker.com/products/docker-desktop/
    echo 安装后启动 Docker Desktop，再运行本脚本。
    echo.
    pause
    exit /b 1
)
echo [OK] Docker 已安装

echo.
echo [1/3] 构建并启动容器（首次约 3-5 分钟）...
echo.
docker-compose up -d
if %errorlevel% neq 0 (
    echo.
    echo [错误] Docker 启动失败！
    echo 请确保 Docker Desktop 正在运行，然后重试。
    pause
    exit /b 1
)

echo.
echo [2/3] 等待服务就绪（约 10 秒）...
timeout /t 12 /nobreak >nul

echo.
echo [3/3] 打开平台页面...
start http://localhost:8000

echo.
echo ============================================================
echo   Docker 部署完成！
echo.
echo   访问地址：http://localhost:8000
echo   API 文档：http://localhost:8000/docs
echo.
echo   常用命令：
echo     查看日志：docker-compose logs -f api
echo     停止服务：docker-compose down
echo     重启服务：docker-compose restart
echo ============================================================
echo.
pause
