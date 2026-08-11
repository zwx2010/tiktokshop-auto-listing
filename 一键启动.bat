@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo ============================================
echo   TikTokShop Platform - One-Click Launcher
echo ============================================
python scripts\launcher.py %*
if errorlevel 1 (
  echo.
  echo [FAILED] launcher exited abnormally. Check python in PATH / deps.
  pause
)
