@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if "%CLIPPER_VENV%"=="" set "CLIPPER_VENV=%~dp0.venv"

if not exist "%CLIPPER_VENV%\Scripts\python.exe" (
  echo Сначала запустите setup.bat
  pause
  exit /b 1
)
if not exist "web\dist\index.html" (
  where npm >nul 2>&1 || (echo Нет web\dist и нет Node.js. Запустите setup.bat. & pause & exit /b 1)
  pushd web
  call npm install --no-audit --no-fund && call npm run build
  popd
)

echo Интерфейс: http://127.0.0.1:8000  (остановить: Ctrl+C)
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:8000'"
"%CLIPPER_VENV%\Scripts\python.exe" -m clipper serve --port 8000
endlocal
