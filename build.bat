@echo off
REM Build Goyim Screener.exe on your own Windows PC (needs Python 3.11+ from python.org).
REM GitHub builds it for you automatically; this is the manual option.
cd /d "%~dp0\.."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller build\ema_zone.spec --noconfirm --clean
echo.
echo Done. The app is in: dist\Goyim Screener\Goyim Screener.exe
pause
