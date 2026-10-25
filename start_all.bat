@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title Crypto Signal Bot - Full Stack
cd /d "%~dp0"
echo ==================================================================
echo   Starting Crypto Signal Bot (All Workers + Dashboard)
echo ==================================================================
call venv\Scripts\activate.bat
python run_all.py
pause

