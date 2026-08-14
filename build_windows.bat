@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Missing .venv. Create it with: py -3.11 -m venv .venv
  exit /b 1
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-build.txt
python -m unittest discover -s tests
if errorlevel 1 exit /b 1

python -m PyInstaller --clean --noconfirm DrugReferAgent.spec
if errorlevel 1 exit /b 1

echo.
echo Build complete: dist\DrugReferAgent.exe
endlocal
