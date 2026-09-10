@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Please run: py -3 -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
  exit /b 1
)
if not exist "frontend\dist\index.html" (
  echo Please run: cd frontend ^&^& npm ci ^&^& npm run build ^&^& cd ..
  exit /b 1
)
if /i "%~1"=="--check" exit /b 0

".venv\Scripts\python.exe" -m uvicorn analyzer.app:create_app --factory --host 127.0.0.1 --port 8765
exit /b %errorlevel%
