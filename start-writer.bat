@echo off
chcp 65001 >nul
title Kepu Writer Server
cd /d "%~dp0"

set "PY=C:\Users\27077\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=python"

echo ==================================================
echo   Kepu Writer - local service
echo ==================================================
echo   Keep this window open while using the workbench.
echo   Open:  http://127.0.0.1:8787/writer.html
echo   Stop:  press Ctrl+C in this window
echo ==================================================
echo.

"%PY%" tools\writer_server.py
echo.
echo Service stopped.
pause
