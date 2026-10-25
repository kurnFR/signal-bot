@echo off
title MariaDB Server (Port 3306)
cd /d "%~dp0"
echo ==================================================================
echo   Starting MariaDB Server for Crypto Signal Bot
echo   Port: 3306 ^| Database: crypto_signals
echo ==================================================================
"..\mariadb\mariadb-10.11.10-winx64\bin\mysqld.exe" --defaults-file="..\mariadb\data\my.ini" --console
pause

