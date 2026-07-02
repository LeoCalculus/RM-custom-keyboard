@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto dependencies

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD if exist "%USERPROFILE%\scoop\apps\python\current\python.exe" set "PYTHON_CMD="%USERPROFILE%\scoop\apps\python\current\python.exe""
if not defined PYTHON_CMD (
    for /f "delims=" %%D in ('dir /b /ad /o-n "%LOCALAPPDATA%\Programs\Python\Python3*" 2^>nul') do if not defined PYTHON_CMD set "PYTHON_CMD="%LOCALAPPDATA%\Programs\Python\%%D\python.exe""
)
if not defined PYTHON_CMD (
    echo ERROR: Python 3 was not found.
    exit /b 1
)

echo Creating local Python environment...
%PYTHON_CMD% -m venv .venv
if errorlevel 1 exit /b 1

:dependencies

".venv\Scripts\python.exe" -c "import UnityPy, yaml" >nul 2>nul
if errorlevel 1 (
    echo Installing extractor dependencies...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 exit /b 1
)

".venv\Scripts\python.exe" extract_ui.py --config config.yaml
if errorlevel 1 exit /b 1

echo.
echo Extraction complete. See the output folder.
exit /b 0
