@echo off
chcp 65001 >nul
title Crypto Signal Bot - Modern UI (React)
cd /d "%~dp0"

set PATH=E:\Project\nodejs;%PATH%

echo ==================================================================
echo   Starting Crypto Signal Bot - Modern React + TypeScript UI
echo   Backend API target: http://127.0.0.1:8050
echo   Modern UI Dev Server: http://localhost:5173
echo ==================================================================

cd frontend
call npm run dev
pause
