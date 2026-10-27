@echo off
setlocal
cd /d "%~dp0"

echo.
echo  =====================================================
echo   EchoVault - OpenRouter Edition
echo  =====================================================
echo.

if not exist .venv\Scripts\python.exe (
  echo [EchoVault] Creating Python virtual environment...
  py -3 -m venv .venv
  if errorlevel 1 (
    echo The Python launcher 'py' was not found or virtual environment creation failed.
    echo Install Python 3.11+ and use the manual setup commands in README.md.
    pause
    exit /b 1
  )
)

if not exist .venv\.echovault-deps-installed (
  echo [EchoVault] Installing dependencies. This first step needs internet access.
  .venv\Scripts\python.exe -m pip install --upgrade pip
  if errorlevel 1 goto install_failed
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 goto install_failed
  echo installed>.venv\.echovault-deps-installed
)

echo.
echo [EchoVault] Checking OpenRouter configuration...
.venv\Scripts\python.exe configure_openrouter.py
if errorlevel 1 (
  echo OpenRouter configuration failed. See README.md for manual steps.
  pause
  exit /b 1
)

echo.
echo [EchoVault] Starting server at http://127.0.0.1:8000
echo Keep this window open while using EchoVault.
echo.
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
exit /b 0

:install_failed
echo.
echo Dependency installation failed. Check your internet connection and see README.md.
pause
exit /b 1
