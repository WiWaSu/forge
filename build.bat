@echo off
chcp 65001 >nul
cd /d "%~dp0"

where pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
  echo PyInstaller не найден. Установить:
  echo     pip install pyinstaller
  echo.
  echo Это инструмент сборки, в готовую программу он не попадает.
  pause
  exit /b 1
)

echo ---------- Тесты перед сборкой ----------
python -m engine.tests.test_calc
python -m engine.tests.test_llm
python -m engine.tests.test_api
echo.

echo ---------- Сборка ----------
pyinstaller --noconfirm --clean forge.spec
if %errorlevel% neq 0 (
  echo.
  echo Сборка не удалась. Текст ошибки выше.
  pause
  exit /b 1
)

echo.
xcopy models dist\models\ /E /I /Y >nul
echo Готово: dist\FORGE.exe
echo Настройки моделей: dist\models\ — правятся без пересборки
echo Папки build\ и __pycache__ можно удалить, они временные.
pause
