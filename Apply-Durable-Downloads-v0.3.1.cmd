@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHON_EXE="
if exist "%LOCALAPPDATA%\Programs\AutoGenBook OpenWebUI\runtime\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Programs\AutoGenBook OpenWebUI\runtime\python.exe"
if not defined PYTHON_EXE if exist "%LOCALAPPDATA%\Programs\AutoGenBook OpenWebUI\venv\Scripts\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Programs\AutoGenBook OpenWebUI\venv\Scripts\python.exe"
if not defined PYTHON_EXE for %%P in (python.exe py.exe) do (
  where %%P >nul 2>nul && if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
)

if not defined PYTHON_EXE (
  echo AutoGenBook durable-download update failed: Python was not found.
  echo Run this command from a full AutoGenBook installer project or pass the Function file manually.
  exit /b 2
)

set "SEARCH_ROOT=%LOCALAPPDATA%\Programs\AutoGenBook OpenWebUI"
if not "%~1"=="" set "SEARCH_ROOT=%~1"

echo Searching AutoGenBook Function under:
echo   %SEARCH_ROOT%
echo.
"%PYTHON_EXE%" "%~dp0tools\apply_openwebui_durable_downloads_v031.py" ^
  --search-root "%SEARCH_ROOT%" ^
  --output-dir "%~dp0dist" ^
  --in-place
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo Automatic patching did not find the Function source.
  echo Export or copy the existing AutoGenBook Function 0.3.0 to this directory and run:
  echo   "%PYTHON_EXE%" tools\apply_openwebui_durable_downloads_v031.py --function-file "PATH\TO\FUNCTION.py" --output-dir dist
  exit /b %RC%
)

echo.
echo Durable-download Function 0.3.1 was created in:
echo   %~dp0dist\AutoGenBook-OpenWebUI-Function-v0.3.1.py
echo.
echo Open WebUI Admin Panel - Functions:
echo   1. Open autogenbook_companion.
echo   2. Replace its whole source with the generated 0.3.1 file.
echo   3. Save, enable it, and restart Open WebUI Desktop.
echo   4. Send: vystupy JOB_ID
exit /b 0
