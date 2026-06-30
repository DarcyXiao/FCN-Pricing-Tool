@echo off
REM ============================================================
REM  Build standalone FCN_Pricing_Tool.exe (no Python required)
REM ============================================================

echo.
echo Installing PyInstaller...
pip install pyinstaller -q

echo.
echo Building standalone executable...
pyinstaller --onefile ^
    --name "FCN_Pricing_Tool" ^
    --add-data "index.html;." ^
    --add-data "docs.html;." ^
    --hidden-import flask ^
    --hidden-import flask_cors ^
    --hidden-import requests ^
    --hidden-import urllib3 ^
    --clean ^
    app.py

echo.
echo ==========================================
echo  Build complete!
echo  Output: dist\FCN_Pricing_Tool.exe
echo ==========================================
pause
