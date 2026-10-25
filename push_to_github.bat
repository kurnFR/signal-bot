@echo off
title Push Updates to GitHub
cd /d "%~dp0"
echo ==================================================================
echo   Pushing committed bug fixes to GitHub (origin/master)...
echo ==================================================================
echo.
git push origin master
echo.
if %ERRORLEVEL% equ 0 (
    echo ==============================================================
    echo   [SUCCESS] Successfully pushed to GitHub!
    echo ==============================================================
) else (
    echo ==============================================================
    echo   [NOTICE] If prompted, please sign in via browser or enter your
    echo   GitHub Personal Access Token (PAT).
    echo ==============================================================
)
echo.
pause
