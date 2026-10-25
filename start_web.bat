@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title Crypto Signal Bot - Web Dashboard
cd /d "%~dp0"
echo ==================================================================
echo   Starting Crypto Signal Bot (Web Dashboard Only)
echo   Open in browser: http://localhost:8050
echo ==================================================================
call venv\Scripts\activate.bat
python run_web.py --port 8050
pause

