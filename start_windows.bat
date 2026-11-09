@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher 'py' was not found. Install Python 3.11+.
  pause
  exit /b 1
)
where npm >nul 2>nul
if errorlevel 1 (
  echo npm was not found. Install Node.js LTS from nodejs.org.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt
if errorlevel 1 (
  echo Backend dependency install failed.
  pause
  exit /b 1
)
if not exist "frontend\node_modules" (
  pushd frontend
  call npm install
  if errorlevel 1 (
    popd
    echo Frontend dependency install failed.
    pause
    exit /b 1
  )
  popd
)
start "ThemeLab Backend" /D "%~dp0backend" "%~dp0.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
start "ThemeLab Frontend" /D "%~dp0frontend" cmd /k npm run dev
start "" "http://127.0.0.1:5173"
echo ThemeLab starting. Keep both terminals open while using it.
endlocal
