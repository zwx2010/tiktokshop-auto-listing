@echo off
chcp 936 >nul
title 导出选品表
cd /d "%~dp0"

echo ==========================================
echo   导出选品专用表  data\products_时间戳.xlsx
echo ==========================================
echo.
python tools\export_products_excel.py
echo.
if errorlevel 1 (
  echo [失败] 导出出错。常见原因：python 不在 PATH、或依赖缺失。
  echo        请先确认 start_server.bat 能启动服务。
) else (
  echo [完成] 选品表已导出到 data 目录。
  echo        下一步：用 Excel 打开它，删掉不要的商品行，
  echo        另存为 data\selected.xlsx，
  echo        然后双击「按选品表出上架表.bat」。
)
echo.
pause
