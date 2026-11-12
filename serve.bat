@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Запускаю FORGE из исходников.
echo Откроется окно программы. Закройте его, чтобы выйти.
echo.
python app.py
if %errorlevel% neq 0 pause
