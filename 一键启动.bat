@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo ============================================
echo   TikTokShop 多账号自动化运营平台 - 一键启动
echo ============================================
python scripts\launcher.py %*
if errorlevel 1 (
  echo.
  echo [失败] 启动器异常退出。请确认 python 在 PATH 中，且已执行过"安装依赖"。
  pause
)
