@echo off
REM Anna'ni ishga tushiradi va yiqilsa 10 soniyadan keyin avtomatik qayta ishga tushiradi.
REM To'xtatish: Ctrl+C, so'ng "Terminate batch job (Y/N)?" savoliga Y.
cd /d "%~dp0"
chcp 65001 >nul
:loop
if exist venv\Scripts\python.exe (
    venv\Scripts\python.exe main.py
) else (
    python main.py
)
echo.
echo [run.bat] Anna to'xtadi. 10 soniyadan keyin qayta ishga tushiriladi (to'xtatish: Ctrl+C)...
timeout /t 10
goto loop
