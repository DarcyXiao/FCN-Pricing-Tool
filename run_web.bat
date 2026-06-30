@echo off
REM ============================================================
REM  FCN Pricing Simulation Tool — Web App Launcher
REM ============================================================

echo.
echo Installing dependencies...
pip install -r "%~dp0requirements.txt" -q

echo.
echo Starting FCN Pricing Simulation server...
echo Open http://127.0.0.1:5000 in your browser.
echo Press Ctrl+C to stop the server.
echo.

python "%~dp0app.py"
pause
