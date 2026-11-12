@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ---------- Расчёты ----------
python -m engine.tests.test_calc
echo.
echo ---------- Нейросеть ----------
python -m engine.tests.test_llm
echo.
echo ---------- Интерфейс ----------
python -m engine.tests.test_api
echo.
pause
