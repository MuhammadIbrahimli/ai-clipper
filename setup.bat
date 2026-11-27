@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if "%CLIPPER_VENV%"=="" set "CLIPPER_VENV=%~dp0.venv"
set "PY=python"
if exist "C:\laragon\bin\python\python-3.13\python.exe" set "PY=C:\laragon\bin\python\python-3.13\python.exe"
if "%PY%"=="python" py -3.13 -c "import sys" >nul 2>&1 && set "PY=py -3.13"

echo [1/5] Проверка Python 3.13
%PY% -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else 1)"
if errorlevel 1 (
  echo Нужен Python 3.13. Установите его или поправьте путь PY в setup.bat.
  exit /b 1
)

echo [2/5] Виртуальное окружение: %CLIPPER_VENV%
if not exist "%CLIPPER_VENV%\Scripts\python.exe" %PY% -m venv "%CLIPPER_VENV%"
if errorlevel 1 exit /b 1

echo [3/5] Python-зависимости
"%CLIPPER_VENV%\Scripts\python.exe" -m pip install --upgrade pip -q
"%CLIPPER_VENV%\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Не удалось установить зависимости.
  exit /b 1
)

echo [4/5] Внешние инструменты
where ffmpeg >nul 2>&1 || echo ПРЕДУПРЕЖДЕНИЕ: ffmpeg не найден в PATH. Укажите ffmpeg_path в config.yaml.
where ffprobe >nul 2>&1 || echo ПРЕДУПРЕЖДЕНИЕ: ffprobe не найден в PATH.
where claude >nul 2>&1 || echo ПРЕДУПРЕЖДЕНИЕ: claude CLI не найден. Отбор клипов не заработает без него.
if exist "tools\platform-tools\adb.exe" (echo adb: tools\platform-tools\adb.exe) else (where adb >nul 2>&1 || echo ПРЕДУПРЕЖДЕНИЕ: adb не найден. Нужен только для Android-публикации.)
if exist "%USERPROFILE%\.cache\huggingface\hub\models--Systran--faster-whisper-small" (
  echo Модель Whisper small: найдена в кэше
) else (
  echo ПРЕДУПРЕЖДЕНИЕ: модель Whisper small не в кэше. Она скачается при первой транскрибации.
)

echo [5/5] Веб-интерфейс
where npm >nul 2>&1
if errorlevel 1 (
  echo ПРЕДУПРЕЖДЕНИЕ: Node.js не найден. Установите Node.js LTS и запустите setup.bat снова, иначе интерфейс не соберётся.
  goto done
)
pushd web
call npm install --no-audit --no-fund
if errorlevel 1 (popd & exit /b 1)
call npm run build
if errorlevel 1 (popd & exit /b 1)
popd

:done
echo Готово. Запуск: run.bat
endlocal
