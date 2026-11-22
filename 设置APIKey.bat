@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1

if not exist "ui.py" (
    echo [ERROR] ui.py not found next to this file.
    echo.
    echo You are probably running it INSIDE the zip file.
    echo Please RIGHT-CLICK the zip - Extract All - then double-click again.
    echo.
    pause
    exit /b 1
)

set "PY="
where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if not defined PY (
    where py >nul 2>nul
    if not errorlevel 1 set "PY=py"
)
if not defined PY (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do set "PY=%%~fD\python.exe"
)
if not defined PY (
    echo [ERROR] Python not found.
    echo Install Python from python.org and TICK "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

%PY% -c "import ui; ui.set_api_key()"
echo.
pause
