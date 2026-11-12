@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >/dev/null 2>nul
if %errorlevel%==0 (
    set "PYEXE=python"
) else (
    where py >/dev/null 2>nul
    if %errorlevel%==0 (
        set "PYEXE=py -3"
    ) else (
        echo.
        echo Python не найден. Установи Python 3.10+ и отметь "Add to PATH".
        echo https://www.python.org/downloads/
        echo.
        pause
        exit /b 1
    )
)

%PYEXE% tools\menu.py %*
if %errorlevel% neq 0 pause
