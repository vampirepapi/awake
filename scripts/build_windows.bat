@echo off
REM Build Awake.exe (and the installer if Inno Setup is installed) on Windows.
REM   scripts\build_windows.bat
REM Output: dist\Awake\Awake.exe, dist\Awake-<version>-Setup.exe
setlocal enableextensions
cd /d "%~dp0\.."

if not exist .venv\Scripts\python.exe (
    py -3.12 -m venv .venv || python -m venv .venv || goto :error
)
.venv\Scripts\python -m pip install --quiet --upgrade pip || goto :error
.venv\Scripts\python -m pip install --quiet -r requirements-dev.txt || goto :error

for /f "delims=" %%v in ('.venv\Scripts\python -c "import awake; print(awake.__version__)"') do set VERSION=%%v

echo ==^> Tests
.venv\Scripts\python -m pytest -q -m "not native" || goto :error

echo ==^> PyInstaller
if exist dist\Awake rmdir /s /q dist\Awake
.venv\Scripts\pyinstaller --noconfirm --clean --distpath dist --workpath build packaging\awake_windows.spec || goto :error

echo ==^> Installer
set ISCC=
where iscc >nul 2>nul && set ISCC=iscc
if "%ISCC%"=="" if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if "%ISCC%"=="" (
    echo Inno Setup not found - skipping installer. Install with: choco install innosetup
) else (
    %ISCC% /Qp /DAppVersion=%VERSION% packaging\installer.iss || goto :error
)

echo ==^> Done: dist\Awake\Awake.exe
exit /b 0

:error
echo Build failed with error %errorlevel%.
exit /b %errorlevel%
