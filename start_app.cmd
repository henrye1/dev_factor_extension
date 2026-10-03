@echo off
rem Double-click to start LGD Tail Extension. Keep this window open while you use the app;
rem closing it stops the app. The browser opens by itself once the app is ready.
title LGD Tail Extension (keep this window open)
cd /d "%~dp0"

rem already running? then just open the browser
curl -s -o nul http://127.0.0.1:8000/api/health && (
  echo The app is already running. Opening the browser.
  start "" http://127.0.0.1:8000
  ping -n 4 127.0.0.1 >nul
  exit /b 0
)

echo Starting the app. The browser opens in a few seconds.
echo Sign in with ADMIN_EMAIL and ADMIN_PASSWORD from the .env file in this folder.
echo.
start "" /min cmd /c "for /l %%i in (1,1,60) do (curl -s -o nul http://127.0.0.1:8000/api/health && (start http://127.0.0.1:8000 & exit) || ping -n 2 127.0.0.1 >nul)"
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_local.ps1"

echo.
echo The app has stopped. If that was not intended, the message above says why.
pause
