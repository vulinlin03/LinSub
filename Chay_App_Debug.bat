@echo off
cd /d "%~dp0"
title LinSub (Kiem tra loi)
echo Dang khoi chay LinSub qua console...
echo Thu muc hien tai: %CD%
echo.

if exist "C:\Python314\python.exe" (
    "C:\Python314\python.exe" "%~dp0linsub_desktop.py"
) else (
    python "%~dp0linsub_desktop.py"
)

echo.
echo [Chuong trinh da dung voi ma thoat: %errorlevel%]
pause
