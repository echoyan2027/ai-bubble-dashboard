@echo off
REM ============================================
REM AI 泡沫仪表盘 - 本地一键更新脚本
REM 作用: 拉取最新数据 → 重新渲染 → 自动打开浏览器
REM ============================================

title AI 泡沫监控仪表盘 - 一键更新

echo ============================================
echo   AI 泡沫监控仪表盘
echo   正在拉取最新数据并重新渲染...
echo ============================================
echo.

cd /d "%~dp0"

REM 跑 Python 更新
D:\python.exe update.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [错误] 更新失败
    echo 请检查: 1) Python 路径是否正确  2) 网络是否通畅
    echo 详细错误: logs\dashboard.log
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   渲染完成!
echo   仪表盘路径: %CD%\data\dashboard.html
echo ============================================
echo.

REM 自动打开浏览器
start "" "%CD%\data\dashboard.html"

echo 按任意键关闭此窗口...
pause >nul
