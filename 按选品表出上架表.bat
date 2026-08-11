@echo off
chcp 936 >nul
title 按选品表出上架表
cd /d "%~dp0"

set "SEL=%~1"
if "%SEL%"=="" set "SEL=data\selected.xlsx"

if not exist "%SEL%" (
  echo 找不到选品表：%SEL%
  echo.
  echo 用法（二选一）：
  echo   1. 把选好的表另存为  data\selected.xlsx  再双击本文件
  echo   2. 直接把选好的 xlsx 拖到本窗口上
  echo.
  pause
  exit /b 1
)

echo 选品表：%SEL%
echo 正在生成上架表（PH 市场，SKU 全出）...
echo.
python tools\export_platform_to_staging.py --selected-file "%SEL%"
echo.
if errorlevel 1 (
  echo [失败] 出表出错，请把窗口里的提示发给 Claude 排查。
) else (
  echo [完成] 上架表在  tk自动化工作流\runs\platform_export_时间戳\ph\ 目录下。
)
echo.
pause
