@echo off
title Wonder Vault Video Generator
setlocal enabledelayedexpansion

:: Check directory structure
if exist "%~dp0MoneyPrinterTurbo-main\webui.bat" (
    cd /d "%~dp0MoneyPrinterTurbo-main"
) else if exist "%~dp0MoneyPrinterTurbo\webui.bat" (
    cd /d "%~dp0MoneyPrinterTurbo"
) else if exist "%~dp0webui.bat" (
    cd /d "%~dp0"
) else (
    echo Error: Could not locate webui.bat in MoneyPrinterTurbo folders.
    pause
    exit /b 1
)

call webui.bat
pause
