@echo off
setlocal
cd /d "%~dp0"
title LinSub

:: Tim pythonw hoac python
set "PY="
if exist "C:\Python314\pythonw.exe" set "PY=C:\Python314\pythonw.exe"
if not defined PY (
    where pythonw.exe >nul 2>&1 && set "PY=pythonw.exe"
)
if not defined PY (
    if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"
)
if not defined PY (
    where python.exe >nul 2>&1 && set "PY=python.exe"
)

if not defined PY (
    echo [LOI] Khong tim thay Python! Vui long cai dat Python.
    pause
    exit /b 1
)

start "" "%PY%" "%~dp0linsub_desktop.py"
exit /b 0
