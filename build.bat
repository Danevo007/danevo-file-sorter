@echo off
setlocal
cd /d "%~dp0"
echo ==== Danevo File Sorter - build ====

where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer was not found. Install it from python.org and tick Add python.exe to PATH.
  exit /b 1
)

if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1

python make_icon.py
if errorlevel 1 exit /b 1

set MODE=--onedir
if /i "%1"=="onefile" set MODE=--onefile

pyinstaller --noconfirm --clean --windowed %MODE% ^
  --name "Danevo File Sorter" ^
  --icon danevo.ico ^
  --version-file version_info.txt ^
  --collect-all customtkinter ^
  --hidden-import pystray._win32 ^
  --add-data "danevo.ico;." ^
  --add-data "danevo.png;." ^
  danevo_file_sorter.py
if errorlevel 1 exit /b 1

echo.
echo ==== DONE ====
if /i "%1"=="onefile" (
  echo   dist\Danevo File Sorter.exe
) else (
  echo   dist\Danevo File Sorter\Danevo File Sorter.exe
  echo   Next: open installer.iss in Inno Setup and press Compile for a Setup.exe
)
