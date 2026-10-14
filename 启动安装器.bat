@echo off
rem Start the local web installer (browser frontend) and open the page.
rem Content is ASCII only so the file encoding never breaks the interpreter path.
setlocal
if exist "%~dp0SengokuRanceInstaller.exe" (
  start "" "%~dp0SengokuRanceInstaller.exe"
  exit /b 0
)
set "APP=%~dp0web_server.py"
if not exist "%APP%" (
  echo web_server.py not found next to this launcher.
  pause
  exit /b 1
)
set "PY="
call :try "pyw -3"
call :try "pythonw"
call :try "python"
if not defined PY (
  echo No Python with Pillow was found.
  echo Install Pillow first, e.g.:  python -m pip install pillow
  pause
  exit /b 1
)
start "" %PY% "%APP%"
exit /b 0

:try
if defined PY exit /b 0
%~1 -c "import PIL" >nul 2>&1
if not errorlevel 1 set "PY=%~1"
exit /b 0
