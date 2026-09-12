@echo off
chcp 65001 >nul
title Kepu Writer
cd /d "%~dp0"

set "PY=C:\Users\27077\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" tools\open_writer.py

echo.
pause

