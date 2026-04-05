@echo off
chcp 65001 >nul
title 酷狗VIP自动领取工具

echo.
echo ========================================
echo   酷狗VIP自动领取工具 启动中...
echo ========================================
echo.

cd /d "%~dp0"

REM 检查Python是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到Python，请先安装Python
    pause
    exit /b 1
)

REM 启动服务
python main.py

pause
